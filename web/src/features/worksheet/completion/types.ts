import type { SqlMetadata } from "@/types/sqlMetadata";

export interface SnapshotColumn {
  name: string;
  type: string;
}

// One catalog's worth of schema/table/column data, in the same shape as the
// active catalog's top-level fields below. Used for every catalog other than
// the active one.
export interface CatalogEntry {
  schemas: string[];
  tablesBySchema: Record<string, string[]>;
  columnsByTable: Record<string, SnapshotColumn[]>;
}

// An in-memory view of the workspace catalog the completion engine reads.
// `columnsByTable` is keyed by "schema.table" and is sparse: only tables whose
// columns have been fetched (lazily) are present.
//
// `schemas`/`tablesBySchema`/`columnsByTable` are always the *active* catalog
// (the one unqualified names resolve against). `catalogs` lists every catalog
// attached to the workspace (including the active one, for fully-qualified
// references); `crossCatalog` holds the same shape as the active catalog's
// fields, one entry per *non-active* catalog, loaded lazily one level at a
// time as a `catalog.`/`catalog.schema.` qualifier is typed.
export interface CatalogSnapshot {
  schemas: string[];
  tablesBySchema: Record<string, string[]>;
  columnsByTable: Record<string, SnapshotColumn[]>;
  catalogs: string[];
  crossCatalog: Record<string, CatalogEntry>;
}

export type SuggestionKind =
  "keyword" | "function" | "column" | "table" | "schema" | "type" | "catalog";

export interface Suggestion {
  label: string;
  kind: SuggestionKind;
  detail?: string;
  documentation?: string;
  insertText?: string;
  sortText?: string;
  // "schema.table" a column came from. Used to keep same-named columns from
  // different joined tables distinct during dedup (not shown directly).
  source?: string;
}

// A table referenced in the current statement's FROM/JOIN clause. `catalog`
// is only set for a fully-qualified `catalog.schema.table` reference — it
// implies `schema` is also set (see `referencedTables`).
export interface TableRef {
  catalog?: string;
  schema?: string;
  table: string;
  alias?: string;
}

export type Clause =
  | "start" // statement start → statement keywords
  | "from" // after FROM/JOIN/INTO/UPDATE → schemas + tables
  | "select" // SELECT/WHERE/GROUP BY/… → columns + functions + keywords
  | "type"; // after CAST(… AS / column def → data types

export interface CursorContext {
  clause: Clause;
  wordPrefix: string;
  // Dotted qualifier before the cursor: ["a"] for `a.col`,
  // ["schema","table"] for `schema.table.col`. Empty when there is no dot.
  qualifier: string[];
  fromTables: TableRef[];
}

export interface CompletionInput {
  text: string;
  offset: number;
  catalog: CatalogSnapshot;
  metadata: SqlMetadata | null;
}
