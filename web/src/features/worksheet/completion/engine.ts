import { getCursorContext } from "./statementContext";
import { FALLBACK_KEYWORDS, STATEMENT_START_KEYWORDS } from "./keywords";
import type {
  CatalogSnapshot,
  CompletionInput,
  Suggestion,
  SuggestionKind,
  TableRef,
} from "./types";
import type { SqlMetadata } from "@/types/sqlMetadata";

// Resolve a table reference to its "schema.table" column-cache key. When the ref
// has no schema, find the first schema that contains a table of that name.
function tableKey(snapshot: CatalogSnapshot, ref: TableRef): string | null {
  if (ref.schema) return `${ref.schema}.${ref.table}`;
  for (const schema of snapshot.schemas) {
    if ((snapshot.tablesBySchema[schema] ?? []).includes(ref.table)) {
      return `${schema}.${ref.table}`;
    }
  }
  return null;
}

function columnSuggestions(
  snapshot: CatalogSnapshot,
  key: string | null,
  // When several tables are in scope (a JOIN), show the source table in the row
  // detail so identically-named columns can be told apart at a glance.
  withSource = false,
): Suggestion[] {
  if (!key) return [];
  return (snapshot.columnsByTable[key] ?? []).map((c) => ({
    label: c.name,
    kind: "column" as const,
    detail: withSource ? `${c.type} · ${key}` : c.type,
    documentation: `from ${key}`,
    source: key,
  }));
}

function keywordSuggestions(metadata: SqlMetadata | null): Suggestion[] {
  const names =
    metadata && metadata.keywords.length > 0
      ? metadata.keywords.map((k) => k.name.toUpperCase())
      : FALLBACK_KEYWORDS;
  return names.map((name) => ({ label: name, kind: "keyword" as const }));
}

function functionSuggestions(metadata: SqlMetadata | null): Suggestion[] {
  if (!metadata) return [];
  return metadata.functions.map((f) => ({
    label: f.name,
    kind: "function" as const,
    detail: f.signature,
    documentation: f.examples ?? undefined,
  }));
}

function typeSuggestions(metadata: SqlMetadata | null): Suggestion[] {
  if (!metadata) return [];
  return metadata.types.map((t) => ({
    label: t.name,
    kind: "type" as const,
    detail: t.category ?? undefined,
  }));
}

function tableSuggestions(snapshot: CatalogSnapshot): Suggestion[] {
  const out: Suggestion[] = [];
  for (const schema of snapshot.schemas) {
    out.push({ label: schema, kind: "schema" });
    for (const table of snapshot.tablesBySchema[schema] ?? []) {
      out.push({ label: table, kind: "table", detail: schema });
    }
  }
  return out;
}

// Suggestions for a dotted qualifier (`a.`, `schema.`, `schema.table.`).
function qualifierSuggestions(
  qualifier: string[],
  clause: string,
  snapshot: CatalogSnapshot,
  fromTables: TableRef[],
): Suggestion[] {
  if (qualifier.length >= 2) {
    const [schema, table] = qualifier.slice(-2);
    return columnSuggestions(snapshot, `${schema}.${table}`);
  }
  const part = qualifier[0];
  // In a FROM clause a single qualifier is a schema → list its tables.
  if (clause === "from") {
    return (snapshot.tablesBySchema[part] ?? []).map((t) => ({
      label: t,
      kind: "table" as const,
      detail: part,
    }));
  }
  // Elsewhere: an alias or table name → that table's columns.
  const ref = fromTables.find((r) => r.alias === part || r.table === part);
  if (ref) return columnSuggestions(snapshot, tableKey(snapshot, ref));
  // Fall back to treating it as a schema (qualified table reference).
  return (snapshot.tablesBySchema[part] ?? []).map((t) => ({
    label: t,
    kind: "table" as const,
    detail: part,
  }));
}

const KIND_RANK: Record<string, Record<SuggestionKind, number>> = {
  from: { schema: 0, table: 1, column: 2, function: 3, keyword: 4, type: 5 },
  select: { column: 0, function: 1, keyword: 2, table: 3, schema: 4, type: 5 },
  type: { type: 0, keyword: 1, function: 2, column: 3, table: 4, schema: 5 },
  start: { keyword: 0, function: 1, column: 2, table: 3, schema: 4, type: 5 },
};

function rankAndFilter(
  raw: Suggestion[],
  prefix: string,
  clause: string,
): Suggestion[] {
  const lower = prefix.toLowerCase();
  const ranks = KIND_RANK[clause] ?? KIND_RANK.select;
  const seen = new Set<string>();
  const out: Suggestion[] = [];
  for (const s of raw) {
    // Columns are deduped per source table so same-named columns from different
    // joined tables both survive; everything else dedups by kind + label.
    const dedupKey =
      s.kind === "column"
        ? `column:${s.label}:${s.source ?? ""}`
        : `${s.kind}:${s.label}`;
    if (seen.has(dedupKey)) continue;
    const labelLower = s.label.toLowerCase();
    if (lower && !labelLower.includes(lower)) continue;
    seen.add(dedupKey);
    const matchRank = !lower || labelLower.startsWith(lower) ? 0 : 1;
    const kindRank = ranks[s.kind];
    out.push({ ...s, sortText: `${matchRank}${kindRank}${s.label}` });
  }
  return out;
}

export function getCompletions(input: CompletionInput): Suggestion[] {
  const { text, offset, catalog, metadata } = input;
  const ctx = getCursorContext(text, offset);

  let raw: Suggestion[];
  if (ctx.qualifier.length > 0) {
    raw = qualifierSuggestions(
      ctx.qualifier,
      ctx.clause,
      catalog,
      ctx.fromTables,
    );
  } else if (ctx.clause === "start") {
    raw = STATEMENT_START_KEYWORDS.map((name) => ({
      label: name,
      kind: "keyword" as const,
    }));
  } else if (ctx.clause === "from") {
    raw = tableSuggestions(catalog);
  } else if (ctx.clause === "type") {
    raw = typeSuggestions(metadata);
  } else {
    // SELECT / WHERE / GROUP BY / … → columns merged from every table in scope.
    const multiTable = ctx.fromTables.length > 1;
    const columns = ctx.fromTables.flatMap((ref) =>
      columnSuggestions(catalog, tableKey(catalog, ref), multiTable),
    );
    // When columns are in scope, don't bury them under the full function/keyword
    // dump: only add those once the user starts typing a prefix to filter by.
    const showFunctions = columns.length === 0 || ctx.wordPrefix.length > 0;
    raw = showFunctions
      ? [
          ...columns,
          ...functionSuggestions(metadata),
          ...keywordSuggestions(metadata),
        ]
      : columns;
  }

  return rankAndFilter(raw, ctx.wordPrefix, ctx.clause);
}

// Whether the suggestions at this cursor depend on catalog data that has not
// loaded yet (lazily-fetched columns, or a schema's table list). The Monaco
// adapter uses this to mark the completion list `incomplete` so it re-queries
// once the data arrives, instead of caching a function-only / empty result.
export function pendingColumns(
  text: string,
  offset: number,
  catalog: CatalogSnapshot,
): boolean {
  const ctx = getCursorContext(text, offset);

  if (ctx.qualifier.length > 0) {
    if (ctx.qualifier.length >= 2) {
      const [schema, table] = ctx.qualifier.slice(-2);
      return !(`${schema}.${table}` in catalog.columnsByTable);
    }
    const part = ctx.qualifier[0];
    if (ctx.clause === "from") {
      return (catalog.tablesBySchema[part] ?? []).length === 0;
    }
    const ref = ctx.fromTables.find(
      (r) => r.alias === part || r.table === part,
    );
    if (ref) {
      const key = tableKey(catalog, ref);
      return !key || !(key in catalog.columnsByTable);
    }
    return (catalog.tablesBySchema[part] ?? []).length === 0;
  }

  if (
    ctx.clause !== "start" &&
    ctx.clause !== "from" &&
    ctx.clause !== "type"
  ) {
    return ctx.fromTables.some((ref) => {
      const key = tableKey(catalog, ref);
      return !key || !(key in catalog.columnsByTable);
    });
  }
  return false;
}
