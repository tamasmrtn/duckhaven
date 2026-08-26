import { del, get, post } from "./client";
import type {
  CatalogSchema,
  CatalogTable,
  TableSnapshot,
} from "@/types/catalog";
import type { QueryRowsPage } from "@/types/query";

// The 8-type set the Create-Table dialog offers; the api enforces the same.
export const ALLOWED_COLUMN_TYPES = [
  "INTEGER",
  "BIGINT",
  "DOUBLE",
  "VARCHAR",
  "BOOLEAN",
  "DATE",
  "TIMESTAMP",
  "DECIMAL",
] as const;

export type AllowedColumnType = (typeof ALLOWED_COLUMN_TYPES)[number];

export interface ColumnSpec {
  name: string;
  type: AllowedColumnType;
  nullable: boolean;
}

// Schemas and tables live under a catalog. The default-catalog shim that let
// this be omitted was removed in api_version 2 — a caller without a catalog has
// nothing to list, so the hooks disable rather than guessing one.
function base(ws: string, catalog: string): string {
  return `/workspaces/${ws}/catalogs/${catalog}/schemas`;
}

export const schemasApi = {
  listSchemas: (ws: string, catalog: string) =>
    get<Pick<CatalogSchema, "name" | "catalog" | "workspace_id">[]>(
      base(ws, catalog),
    ),

  listTables: (ws: string, catalog: string, schema: string) =>
    get<CatalogTable[]>(`${base(ws, catalog)}/${schema}/tables`),

  getTable: (ws: string, catalog: string, schema: string, table: string) =>
    get<CatalogTable>(`${base(ws, catalog)}/${schema}/tables/${table}`),

  sampleRows: (ws: string, catalog: string, schema: string, table: string) =>
    get<QueryRowsPage>(`${base(ws, catalog)}/${schema}/tables/${table}/sample`),

  tableSnapshots: (
    ws: string,
    catalog: string,
    schema: string,
    table: string,
  ) =>
    get<TableSnapshot[]>(
      `${base(ws, catalog)}/${schema}/tables/${table}/snapshots`,
    ),

  createSchema: (ws: string, catalog: string, name: string) =>
    post<{ name: string; catalog: string; catalog_name: string }>(
      base(ws, catalog),
      { name },
    ),

  // Probe row counts for tables that don't have one yet (e.g. created from the
  // worksheet). Returns how many tables were probed.
  refreshStats: (ws: string, catalog: string) =>
    post<{ probed: number }>(
      `/workspaces/${ws}/catalogs/${catalog}/refresh-stats`,
      {},
    ),

  // Force a fresh row-count probe for one table, even if it already has a count.
  recountTable: (ws: string, catalog: string, schema: string, table: string) =>
    post<{ row_count: number | null }>(
      `${base(ws, catalog)}/${schema}/tables/${table}/recount`,
      {},
    ),

  dropSchema: (ws: string, catalog: string, schema: string, cascade = false) =>
    del(`${base(ws, catalog)}/${schema}${cascade ? "?cascade=true" : ""}`),

  createTable: (
    ws: string,
    catalog: string,
    schema: string,
    body: { name: string; columns: ColumnSpec[] },
  ) => post<CatalogTable>(`${base(ws, catalog)}/${schema}/tables`, body),

  deleteTable: (ws: string, catalog: string, schema: string, table: string) =>
    del(`${base(ws, catalog)}/${schema}/tables/${table}`),
};
