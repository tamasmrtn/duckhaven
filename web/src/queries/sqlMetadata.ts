import { useQuery } from "@tanstack/react-query";
import { sqlMetadataApi } from "@/api/sqlMetadata";

// The DuckDB function/keyword/type dictionary is static per agent version, so
// cache it for the session — per agent, since agents on different runtimes run
// different DuckDB versions. A 503 (no agent connected) is left as an error —
// the editor falls back to its static keyword list rather than caching empties.
export function useSqlMetadata(ws: string, agentId?: string) {
  return useQuery({
    queryKey: ["workspace", ws, "sql-metadata", agentId ?? "auto"],
    queryFn: () => sqlMetadataApi.get(ws, agentId),
    enabled: !!ws,
    staleTime: Infinity,
    retry: false,
  });
}
