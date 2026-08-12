import { getCursorContext } from "./statementContext";
import { FALLBACK_KEYWORDS, STATEMENT_START_KEYWORDS } from "./keywords";
import type {
  CatalogSnapshot,
  CompletionInput,
  CursorContext,
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

// DuckDB `duckdb_functions()` categorizes functions by `type` — this project
// only ever offers a completion for the callable-anywhere kinds by default;
// table-position callers (see `getCompletions`'s "from" branch) pass
// `typeFilter` to narrow to the table-valued kinds instead.
function functionSuggestions(
  metadata: SqlMetadata | null,
  typeFilter?: (type: string) => boolean,
): Suggestion[] {
  if (!metadata) return [];
  const fns = typeFilter
    ? metadata.functions.filter((f) => typeFilter(f.type))
    : metadata.functions;
  return fns.map((f) => ({
    label: f.name,
    kind: "function" as const,
    detail: f.signature,
    documentation: f.examples ?? undefined,
  }));
}

const TABLE_VALUED_FUNCTION_TYPES = new Set(["table", "table_macro"]);

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
  for (const cat of snapshot.catalogs) {
    out.push({ label: cat, kind: "catalog" });
  }
  for (const schema of snapshot.schemas) {
    out.push({ label: schema, kind: "schema" });
    for (const table of snapshot.tablesBySchema[schema] ?? []) {
      out.push({ label: table, kind: "table", detail: schema });
    }
  }
  return out;
}

// Suggestions for a dotted qualifier (`a.`, `schema.`, `schema.table.`,
// `catalog.schema.`, `catalog.schema.table.`). A qualifier's first segment is
// checked against the known catalog list before assuming it's a schema —
// matching how DuckDB itself resolves a qualified name (catalog membership is
// checked first, not assumed positionally).
function qualifierSuggestions(
  qualifier: string[],
  clause: string,
  snapshot: CatalogSnapshot,
  fromTables: TableRef[],
): Suggestion[] {
  const [head, ...rest] = qualifier;
  const isCatalog = snapshot.catalogs.includes(head);

  if (isCatalog) {
    const entry = snapshot.crossCatalog[head];
    if (rest.length === 0) {
      // `catalog.` → that catalog's schemas.
      return (entry?.schemas ?? []).map((s) => ({
        label: s,
        kind: "schema" as const,
        detail: head,
      }));
    }
    if (rest.length === 1) {
      // `catalog.schema.` → that schema's tables.
      return (entry?.tablesBySchema[rest[0]] ?? []).map((t) => ({
        label: t,
        kind: "table" as const,
        detail: `${head}.${rest[0]}`,
      }));
    }
    // `catalog.schema.table.` → that table's columns.
    const [schema, table] = rest.slice(-2);
    return (entry?.columnsByTable[`${schema}.${table}`] ?? []).map((c) => ({
      label: c.name,
      kind: "column" as const,
      detail: c.type,
      documentation: `from ${head}.${schema}.${table}`,
      source: `${head}.${schema}.${table}`,
    }));
  }

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
  // Catalog first, then schema, then table: the broadest-to-narrowest object
  // hierarchy, matching how a qualified name is actually built up.
  from: {
    catalog: 0,
    schema: 1,
    table: 2,
    column: 3,
    function: 4,
    keyword: 5,
    type: 6,
  },
  select: {
    column: 0,
    function: 1,
    keyword: 2,
    table: 3,
    schema: 4,
    type: 5,
    catalog: 6,
  },
  type: {
    type: 0,
    keyword: 1,
    function: 2,
    column: 3,
    table: 4,
    schema: 5,
    catalog: 6,
  },
  start: {
    keyword: 0,
    function: 1,
    column: 2,
    table: 3,
    schema: 4,
    type: 5,
    catalog: 6,
  },
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
  return completionsForContext(
    getCursorContext(text, offset),
    catalog,
    metadata,
  );
}

// Same as `getCompletions`, but takes an already-computed cursor context.
// `provider.ts` computes the context once per completion request (it also
// needs it for cross-catalog lazy-loading) and reuses it here and in
// `pendingColumnsForContext`, instead of each recomputing it independently.
export function completionsForContext(
  ctx: CursorContext,
  catalog: CatalogSnapshot,
  metadata: SqlMetadata | null,
): Suggestion[] {
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
    // The regex-based clause detector has no notion of "the FROM target is
    // now complete" — it just sees "from" as the last clause keyword until an
    // actual WHERE/GROUP BY/… is fully typed. So once at least one table has
    // already been parsed out of this FROM (the common case: the user is now
    // past it, starting the next word) also offer clause keywords, not just
    // more tables — otherwise typing "w" toward WHERE suggests nothing.
    //
    // Table-valued functions (read_csv(...), range(...), …) are also legal
    // FROM targets — but a DuckDB instance can report 100+ of them, so
    // (mirroring the same call below for SELECT-position functions/keywords)
    // both of these are only added once a prefix narrows the list, instead of
    // dumping the full set into every bare `FROM `.
    const showExtras = ctx.wordPrefix.length > 0 && ctx.fromTables.length > 0;
    raw = [
      ...tableSuggestions(catalog),
      ...(showExtras
        ? [
            ...functionSuggestions(metadata, (t) =>
              TABLE_VALUED_FUNCTION_TYPES.has(t),
            ),
            ...keywordSuggestions(metadata),
          ]
        : []),
    ];
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
  return pendingColumnsForContext(getCursorContext(text, offset), catalog);
}

// Same as `pendingColumns`, but takes an already-computed cursor context —
// see `completionsForContext`.
export function pendingColumnsForContext(
  ctx: CursorContext,
  catalog: CatalogSnapshot,
): boolean {
  if (ctx.qualifier.length > 0) {
    const [head, ...rest] = ctx.qualifier;
    if (catalog.catalogs.includes(head)) {
      const entry = catalog.crossCatalog[head];
      if (!entry) return true;
      if (rest.length === 0) return entry.schemas.length === 0;
      if (rest.length === 1) {
        return (entry.tablesBySchema[rest[0]] ?? []).length === 0;
      }
      const [schema, table] = rest.slice(-2);
      return !(`${schema}.${table}` in entry.columnsByTable);
    }
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
