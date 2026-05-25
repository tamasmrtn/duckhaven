import { del, get, post } from "./client";
import type { CatalogSchema, CatalogTable } from "@/types/catalog";
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

  createSchema: (ws: string, name: string) =>
    post<{ name: string; catalog_name: string }>(`/workspaces/${ws}/schemas`, {
      name,
    }),

  createTable: (
    ws: string,
    schema: string,
    body: { name: string; columns: ColumnSpec[] },
  ) => post<CatalogTable>(`/workspaces/${ws}/schemas/${schema}/tables`, body),

  deleteTable: (ws: string, schema: string, table: string) =>
    del(`/workspaces/${ws}/schemas/${schema}/tables/${table}`),
};
