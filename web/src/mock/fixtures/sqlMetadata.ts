import type { SqlMetadata } from "@/types/sqlMetadata";

// A small, deterministic DuckDB metadata dictionary for tests and the mock UI.
export const SQL_METADATA: SqlMetadata = {
  functions: [
    {
      name: "count",
      type: "aggregate",
      return_type: "BIGINT",
      signature: "count(x ANY) → BIGINT",
      examples: "count(*)",
    },
    {
      name: "sum",
      type: "aggregate",
      return_type: "HUGEINT",
      signature: "sum(arg NUMERIC) → HUGEINT",
      examples: "sum(amount)",
    },
    {
      name: "upper",
      type: "scalar",
      return_type: "VARCHAR",
      signature: "upper(string VARCHAR) → VARCHAR",
      examples: "upper('abc')",
    },
  ],
  keywords: [
    { name: "select", category: "reserved" },
    { name: "from", category: "reserved" },
    { name: "where", category: "reserved" },
    { name: "group", category: "reserved" },
    { name: "order", category: "reserved" },
  ],
  types: [
    { name: "INTEGER", category: "NUMERIC" },
    { name: "VARCHAR", category: "STRING" },
    { name: "TIMESTAMP", category: "DATETIME" },
  ],
};
