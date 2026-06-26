import { get } from "./client";
import type { SqlMetadata } from "@/types/sqlMetadata";

export const sqlMetadataApi = {
  get: (ws: string) => get<SqlMetadata>(`/workspaces/${ws}/sql-metadata`),
};
