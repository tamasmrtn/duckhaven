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

// Build the schemas base path. With a catalog slug it targets the canonical
// catalog-scoped route; without one it falls back to the legacy default-catalog
// shim (kept for backward compatibility).
function base(ws: string, catalog?: string): string {
  return catalog
    ? `/workspaces/${ws}/catalogs/${catalog}/schemas`
    : `/workspaces/${ws}/schemas`;
}

export const schemasApi = {
  listSchemas: (ws: string, catalog?: string) =>
    get<Pick<CatalogSchema, "name" | "catalog" | "workspace_id">[]>(
      base(ws, catalog),
    ),

  listTables: (ws: string, catalog: string | undefined, schema: string) =>
    get<CatalogTable[]>(`${base(ws, catalog)}/${schema}/tables`),

  getTable: (
    ws: string,
    catalog: string | undefined,
    schema: string,
    table: string,
  ) => get<CatalogTable>(`${base(ws, catalog)}/${schema}/tables/${table}`),

  sampleRows: (
    ws: string,
    catalog: string | undefined,
    schema: string,
    table: string,
  ) =>
    get<QueryRowsPage>(`${base(ws, catalog)}/${schema}/tables/${table}/sample`),

  tableSnapshots: (
    ws: string,
    catalog: string | undefined,
    schema: string,
    table: string,
  ) =>
    get<TableSnapshot[]>(
      `${base(ws, catalog)}/${schema}/tables/${table}/snapshots`,
    ),

  createSchema: (ws: string, catalog: string | undefined, name: string) =>
    post<{ name: string; catalog: string; catalog_name: string }>(
      base(ws, catalog),
      { name },
    ),

  // Probe row counts for tables that don't have one yet (e.g. created from the
  // worksheet). Returns how many tables were probed.
  refreshStats: (ws: string, catalog?: string) =>
    post<{ probed: number }>(`${base(ws, catalog)}/refresh-stats`, {}),

  // Force a fresh row-count probe for one table, even if it already has a count.
  recountTable: (
    ws: string,
    catalog: string | undefined,
    schema: string,
    table: string,
  ) =>
    post<{ row_count: number | null }>(
      `${base(ws, catalog)}/${schema}/tables/${table}/recount`,
      {},
    ),

  dropSchema: (
    ws: string,
    catalog: string | undefined,
    schema: string,
    cascade = false,
  ) => del(`${base(ws, catalog)}/${schema}${cascade ? "?cascade=true" : ""}`),

  createTable: (
    ws: string,
    catalog: string | undefined,
    schema: string,
    body: { name: string; columns: ColumnSpec[] },
  ) => post<CatalogTable>(`${base(ws, catalog)}/${schema}/tables`, body),

  deleteTable: (
    ws: string,
    catalog: string | undefined,
    schema: string,
    table: string,
  ) => del(`${base(ws, catalog)}/${schema}/tables/${table}`),
};
