import type { Monaco } from "@monaco-editor/react";
import type { languages, editor, Position, IRange } from "monaco-editor";
import { activeStatement } from "../statements";
import { getCompletions } from "./engine";
import { referencedTables } from "./statementContext";
import type { CatalogSnapshot, SuggestionKind } from "./types";
import type { SqlMetadata } from "@/types/sqlMetadata";

export interface ColumnRef {
  schema?: string;
  table: string;
}

interface CompletionContext {
  snapshot: CatalogSnapshot;
  metadata: SqlMetadata | null;
  ensureColumns: (refs: ColumnRef[]) => void;
}

const EMPTY_SNAPSHOT: CatalogSnapshot = {
  schemas: [],
  tablesBySchema: {},
  columnsByTable: {},
};

// Module-level singleton: the providers are registered once for the whole app
// (Monaco language providers are global) and read the latest context through
// this mutable ref, so editor remounts never re-register or go stale.
const context: CompletionContext = {
  snapshot: EMPTY_SNAPSHOT,
  metadata: null,
  ensureColumns: () => {},
};

let registered = false;

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
  }
}

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

      // Lazily pull columns for tables referenced in this statement.
      const stmt = activeStatement(text, offset);
      context.ensureColumns(referencedTables(stmt));

      const word = model.getWordUntilPosition(position);
      const range: IRange = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      };

      const suggestions = getCompletions({
        text,
        offset,
        catalog: context.snapshot,
        metadata: context.metadata,
      }).map((s) => ({
        label: s.label,
        kind: mapKind(monaco, s.kind),
        detail: s.detail,
        documentation: s.documentation,
        insertText: s.insertText ?? s.label,
        sortText: s.sortText,
        range,
      }));

      return { suggestions };
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
