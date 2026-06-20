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

export const schemasApi = {
  listSchemas: (ws: string) =>
    get<Pick<CatalogSchema, "name" | "workspace_id">[]>(
      `/workspaces/${ws}/schemas`,
    ),

  listTables: (ws: string, schema: string) =>
    get<CatalogTable[]>(`/workspaces/${ws}/schemas/${schema}/tables`),

  getTable: (ws: string, schema: string, table: string) =>
    get<CatalogTable>(`/workspaces/${ws}/schemas/${schema}/tables/${table}`),

  sampleRows: (ws: string, schema: string, table: string) =>
    get<QueryRowsPage>(
      `/workspaces/${ws}/schemas/${schema}/tables/${table}/sample`,
    ),

  tableSnapshots: (ws: string, schema: string, table: string) =>
    get<TableSnapshot[]>(
      `/workspaces/${ws}/schemas/${schema}/tables/${table}/snapshots`,
    ),

  createSchema: (ws: string, name: string) =>
    post<{ name: string; catalog_name: string }>(`/workspaces/${ws}/schemas`, {
      name,
    }),

  // Probe row counts for tables that don't have one yet (e.g. created from the
  // worksheet). Returns how many tables were probed.
  refreshStats: (ws: string) =>
    post<{ probed: number }>(`/workspaces/${ws}/schemas/refresh-stats`, {}),

  // Force a fresh row-count probe for one table, even if it already has a count.
  recountTable: (ws: string, schema: string, table: string) =>
    post<{ row_count: number | null }>(
      `/workspaces/${ws}/schemas/${schema}/tables/${table}/recount`,
      {},
    ),

  dropSchema: (ws: string, schema: string, cascade = false) =>
    del(`/workspaces/${ws}/schemas/${schema}${cascade ? "?cascade=true" : ""}`),

  createTable: (
    ws: string,
    schema: string,
    body: { name: string; columns: ColumnSpec[] },
  ) => post<CatalogTable>(`/workspaces/${ws}/schemas/${schema}/tables`, body),

  deleteTable: (ws: string, schema: string, table: string) =>
    del(`/workspaces/${ws}/schemas/${schema}/tables/${table}`),
};
