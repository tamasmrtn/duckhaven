import { useQuery } from "@tanstack/react-query";
import { lineageApi } from "@/api/lineage";
import type { LineageDirection } from "@/types/lineage";

// Direction and depth are part of the key: they change which subgraph the API
// returns, so a widened view must not read a narrower one out of the cache.

export function useTableLineage(
  ws: string,
  catalog: string,
  schema: string,
  table: string,
  direction: LineageDirection = "both",
  depth = 2,
  enabled = true,
) {
  return useQuery({
    queryKey: [
      "workspace",
      ws,
      "catalog",
      catalog,
      "schema",
      schema,
      "table",
      table,
      "lineage",
      direction,
      depth,
    ],
    queryFn: () =>
      lineageApi.tableLineage(ws, catalog, schema, table, { direction, depth }),
    enabled: enabled && !!ws && !!catalog && !!schema && !!table,
  });
}
