import { get } from "./client";
import type { CatalogSchema, CatalogTable } from "@/types/catalog";
import type { QueryRowsPage } from "@/types/query";

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
};
