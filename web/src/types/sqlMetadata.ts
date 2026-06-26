// DuckDB SQL metadata for editor autocomplete, sourced from a connected agent
// (api GET /workspaces/{ws}/sql-metadata). Static per DuckDB version.

export interface SqlFunction {
  name: string;
  type: string;
  return_type: string | null;
  signature: string;
  examples: string | null;
}

export interface SqlKeyword {
  name: string;
  category: string | null;
}

export interface SqlType {
  name: string;
  category: string | null;
}

export interface SqlMetadata {
  functions: SqlFunction[];
  keywords: SqlKeyword[];
  types: SqlType[];
}
