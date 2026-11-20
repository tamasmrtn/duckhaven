import { useQuery } from "@tanstack/react-query";
import { systemCatalogApi } from "@/api/systemCatalog";

/**
 * The `duckhaven` system catalog's tables the caller may read: each table takes
 * its own permission, and the server lists only those the caller holds. Where
 * the workspace cannot read it (the server answers 404), `data` stays undefined.
 */
export function useSystemCatalog(ws: string) {
  return useQuery({
    queryKey: ["workspace", ws, "system-catalog"],
    queryFn: () => systemCatalogApi.get(ws),
    enabled: !!ws,
    retry: false,
    // Fixed tables: only `available` changes, and a query says so when it fails.
    staleTime: 5 * 60_000,
  });
}

/** A system table's first rows, for its page. It runs on an agent, as any
 * table's preview does. */
export function useSystemTableSample(
  ws: string,
  schema: string,
  table: string,
) {
  return useQuery({
    queryKey: ["workspace", ws, "system-catalog", schema, table, "sample"],
    queryFn: () => systemCatalogApi.sampleRows(ws, schema, table),
    enabled: !!ws && !!schema && !!table,
    // A refusal or a missing agent is not worth retrying: say so at once.
    retry: false,
  });
}
