import type { Monaco } from "@monaco-editor/react";
import type { languages, editor, Position, IRange } from "monaco-editor";
import { formatRowCount } from "@/utils";
import { completionsForContext, pendingColumnsForContext } from "./engine";
import { getCursorContext } from "./statementContext";
import type { CatalogSnapshot, SuggestionKind } from "./types";
import type { SqlMetadata } from "@/types/sqlMetadata";

export interface ColumnRef {
  schema?: string;
  table: string;
}

// A table's row count, resolved lazily. `exact` distinguishes a real (agent-
// probed) count from the free Iceberg-snapshot estimate, so the UI never
// implies more precision than it has.
export interface RowCountInfo {
  count: number;
  exact: boolean;
}

interface CompletionContext {
  snapshot: CatalogSnapshot;
  metadata: SqlMetadata | null;
  ensureColumns: (refs: ColumnRef[]) => void;
  // Lazily load a non-active catalog's schema list / a schema's table list /
  // a table's columns, mirroring `ensureColumns` one level up — called as a
  // `catalog.`/`catalog.schema.`/`catalog.schema.table.` qualifier is typed.
  ensureCrossCatalogSchemas: (catalogName: string) => void;
  ensureCrossCatalogTables: (catalogName: string, schema: string) => void;
  ensureCrossCatalogColumns: (
    catalogName: string,
    schema: string,
    table: string,
  ) => void;
  // Lazily fetch one table's row count for the completion-detail panel,
  // fired from `resolveCompletionItem` for whichever table suggestion is
  // currently highlighted — never for the whole list at once.
  ensureTableDetail: (schema: string, table: string) => void;
  rowCountByTable: Record<string, RowCountInfo | null>;
}

const EMPTY_SNAPSHOT: CatalogSnapshot = {
  schemas: [],
  tablesBySchema: {},
  columnsByTable: {},
  catalogs: [],
  crossCatalog: {},
};

// Module-level singleton: the providers are registered once for the whole app
// (Monaco language providers are global) and read the latest context through
// this mutable ref, so editor remounts never re-register or go stale.
const context: CompletionContext = {
  snapshot: EMPTY_SNAPSHOT,
  metadata: null,
  ensureColumns: () => {},
  ensureCrossCatalogSchemas: () => {},
  ensureCrossCatalogTables: () => {},
  ensureCrossCatalogColumns: () => {},
  ensureTableDetail: () => {},
  rowCountByTable: {},
};

let registered = false;

// The live editor, captured on mount, so the data layer can refresh an open
// suggest widget once lazily-fetched columns arrive (see retriggerSuggest).
let activeEditor: editor.IStandaloneCodeEditor | null = null;

export function setActiveEditor(ed: editor.IStandaloneCodeEditor | null): void {
  activeEditor = ed;
}

// Re-run completion if the suggest widget is currently open. Called when the
// catalog snapshot gains columns so a held-open Ctrl+Space fills in without the
// user having to type. No-op when the widget is closed (so it never pops the
// widget unprompted).
export function retriggerSuggest(): void {
  const ed = activeEditor;
  if (!ed) return;
  const controller = ed.getContribution("editor.contrib.suggestController") as {
    model?: { state?: number };
  } | null;
  // state 0 = idle/closed; only refresh while the widget is actually showing.
  if (!controller?.model || !controller.model.state) return;
  ed.trigger("completion", "editor.action.triggerSuggest", {});
}

export function updateCompletionContext(
  next: Partial<CompletionContext>,
): void {
  Object.assign(context, next);
}

function mapKind(
  monaco: Monaco,
  kind: SuggestionKind,
): languages.CompletionItemKind {
  const K = monaco.languages.CompletionItemKind;
  switch (kind) {
    case "keyword":
      return K.Keyword;
    case "function":
      return K.Function;
    case "column":
      return K.Field;
    case "table":
      return K.Struct;
    case "schema":
      return K.Module;
    case "type":
      return K.TypeParameter;
    case "catalog":
      return K.Module;
  }
}

// A completion item for a table suggestion carries its schema/table so
// `resolveCompletionItem` can look up (and lazily fetch) its row count
// without re-deriving it from `detail`/`label` string parsing.
type TableCompletionItem = languages.CompletionItem & {
  schemaRef?: { schema: string; table: string };
};

// The inner parameter list of a `name(a TYPE, b TYPE) → ret` signature.
function signatureParams(signature: string): string[] {
  const open = signature.indexOf("(");
  const close = signature.lastIndexOf(")");
  if (open < 0 || close <= open) return [];
  const inner = signature.slice(open + 1, close).trim();
  return inner ? inner.split(", ") : [];
}

// Walk back from the cursor to the enclosing function call, returning its name
// and the zero-based index of the argument being typed.
function findActiveCall(
  before: string,
): { name: string; activeParameter: number } | null {
  let depth = 0;
  let commas = 0;
  for (let i = before.length - 1; i >= 0; i--) {
    const ch = before[i];
    if (ch === ")") {
      depth++;
    } else if (ch === "(") {
      if (depth === 0) {
        let j = i;
        while (j > 0 && /[A-Za-z0-9_$]/.test(before[j - 1])) j--;
        const name = before.slice(j, i);
        return name ? { name, activeParameter: commas } : null;
      }
      depth--;
    } else if (ch === "," && depth === 0) {
      commas++;
    }
  }
  return null;
}

export function registerSqlProviders(monaco: Monaco): void {
  if (registered) return;
  registered = true;

  monaco.languages.registerCompletionItemProvider("sql", {
    triggerCharacters: [".", " "],
    provideCompletionItems(
      model: editor.ITextModel,
      position: Position,
    ): languages.ProviderResult<languages.CompletionList> {
      const text = model.getValue();
      const offset = model.getOffsetAt(position);

      // Computed once per completion request and reused below (lazy-loading,
      // suggestions, the incomplete flag) — cursor-context parsing masks and
      // regex-scans the active statement, not cheap enough to redo several
      // times per keystroke.
      const ctx = getCursorContext(text, offset);

      // Lazily pull columns for tables referenced in this statement — a
      // fully-qualified `catalog.schema.table` ref needs the cross-catalog
      // fetch (it lives outside the active catalog's own column cache).
      context.ensureColumns(ctx.fromTables.filter((r) => !r.catalog));
      for (const ref of ctx.fromTables) {
        if (ref.catalog && ref.schema) {
          context.ensureCrossCatalogColumns(ref.catalog, ref.schema, ref.table);
        }
      }

      // Lazily pull a non-active catalog's schema/table/column data once its
      // name appears as the first segment of a dotted qualifier, one level
      // at a time as the qualifier gets longer.
      const [qHead, ...qRest] = ctx.qualifier;
      if (qHead && context.snapshot.catalogs.includes(qHead)) {
        if (qRest.length === 0) {
          context.ensureCrossCatalogSchemas(qHead);
        } else if (qRest.length === 1) {
          context.ensureCrossCatalogTables(qHead, qRest[0]);
        } else {
          const [schema, table] = qRest.slice(-2);
          context.ensureCrossCatalogColumns(qHead, schema, table);
        }
      }

      const word = model.getWordUntilPosition(position);
      const range: IRange = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      };

      const suggestions = completionsForContext(
        ctx,
        context.snapshot,
        context.metadata,
      ).map((s) => {
        // Functions insert as a snippet with the cursor placed inside the
        // parens, ready to type an argument (and trigger signature help via
        // the `(`/`,` triggers below) instead of landing after a bare name.
        const isFunction = s.kind === "function";
        const item: TableCompletionItem = {
          label: s.label,
          kind: mapKind(monaco, s.kind),
          detail: s.detail,
          documentation: s.documentation,
          insertText: s.insertText ?? (isFunction ? `${s.label}($1)` : s.label),
          insertTextRules: isFunction
            ? monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet
            : undefined,
          sortText: s.sortText,
          range,
        };
        // Row count is resolved lazily on hover/highlight (see
        // `resolveCompletionItem` below), only for the active catalog's own
        // tables — `detail` there is a bare schema name; a cross-catalog
        // table suggestion's `detail` is "catalog.schema" instead.
        if (s.kind === "table" && s.detail && !s.detail.includes(".")) {
          item.schemaRef = { schema: s.detail, table: s.label };
        }
        return item;
      });

      // Mark the list incomplete while the columns/tables it depends on are
      // still loading, so Monaco re-queries this provider once they arrive
      // instead of caching a function-only (or empty) result.
      const incomplete = pendingColumnsForContext(ctx, context.snapshot);
      return { suggestions, incomplete };
    },
    // Fires only for the item currently highlighted in an open suggest
    // list — the right place to fetch a per-table row count on demand
    // instead of bulk-fetching one for every table candidate up front.
    resolveCompletionItem(
      item: TableCompletionItem,
    ): languages.ProviderResult<languages.CompletionItem> {
      const ref = item.schemaRef;
      if (!ref) return item;
      context.ensureTableDetail(ref.schema, ref.table);
      const info = context.rowCountByTable[`${ref.schema}.${ref.table}`];
      // `undefined` = not loaded yet (first resolve call, fetch just kicked
      // off); `null` = loaded, no count available. Only a resolved count
      // gets rendered.
      if (info) {
        item.documentation = `${info.exact ? "" : "~"}${formatRowCount(info.count)} rows`;
      }
      return item;
    },
  });

  monaco.languages.registerSignatureHelpProvider("sql", {
    signatureHelpTriggerCharacters: ["(", ","],
    provideSignatureHelp(
      model: editor.ITextModel,
      position: Position,
    ): languages.ProviderResult<languages.SignatureHelpResult> {
      const functions = context.metadata?.functions;
      if (!functions || functions.length === 0) return null;

      const before = model.getValueInRange({
        startLineNumber: 1,
        startColumn: 1,
        endLineNumber: position.lineNumber,
        endColumn: position.column,
      });
      const call = findActiveCall(before);
      if (!call) return null;

      const fn = functions.find(
        (f) => f.name.toLowerCase() === call.name.toLowerCase(),
      );
      if (!fn) return null;

      const params = signatureParams(fn.signature);
      return {
        value: {
          signatures: [
            {
              label: fn.signature,
              documentation: fn.examples ?? undefined,
              parameters: params.map((label) => ({ label })),
            },
          ],
          activeSignature: 0,
          activeParameter: Math.min(
            call.activeParameter,
            Math.max(params.length - 1, 0),
          ),
        },
        dispose() {},
      };
    },
  });
}
