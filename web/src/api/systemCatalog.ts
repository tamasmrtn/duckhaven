import { get } from "./client";
import type { SystemCatalog } from "@/types/systemCatalog";
import type { QueryRowsPage } from "@/types/query";

export const systemCatalogApi = {
  get: (ws: string) => get<SystemCatalog>(`/workspaces/${ws}/system-catalog`),
  sampleRows: (ws: string, schema: string, table: string) =>
    get<QueryRowsPage>(
      `/workspaces/${ws}/system-catalog/schemas/${schema}/tables/${table}/sample`,
    ),
};
