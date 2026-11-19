import { get } from "./client";
import type { SystemCatalog } from "@/types/systemCatalog";

export const systemCatalogApi = {
  get: (ws: string) => get<SystemCatalog>(`/workspaces/${ws}/system-catalog`),
};
