import type { SqlMetadata } from "@/types/sqlMetadata";

export interface SnapshotColumn {
  name: string;
  type: string;
}

// An in-memory view of the workspace catalog the completion engine reads.
// `columnsByTable` is keyed by "schema.table" and is sparse: only tables whose
// columns have been fetched (lazily) are present.
export interface CatalogSnapshot {
  schemas: string[];
  tablesBySchema: Record<string, string[]>;
  columnsByTable: Record<string, SnapshotColumn[]>;
}

export type SuggestionKind =
  | "keyword"
  | "function"
  | "column"
  | "table"
  | "schema"
  | "type";

export interface Suggestion {
  label: string;
  kind: SuggestionKind;
  detail?: string;
  documentation?: string;
  insertText?: string;
  sortText?: string;
}

// A table referenced in the current statement's FROM/JOIN clause.
export interface TableRef {
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
