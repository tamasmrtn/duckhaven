import { useQuery } from "@tanstack/react-query";
import { lineageApi } from "@/api/lineage";
import type { LineageDirection } from "@/types/lineage";

// Direction and depth are part of the key: they change which subgraph the API
// returns, so a widened view must not read a narrower one out of the cache.
//
// So is the set of nodes whose column detail was asked for. It is sorted into
// the key rather than used as given, because expanding A then B has to hit the
// same cache entry as expanding B then A — they are the same request.

export function useTableLineage(
  ws: string,
  catalog: string,
  schema: string,
  table: string,
  direction: LineageDirection = "both",
  depth = 2,
  enabled = true,
  columnsFor: string[] = [],
) {
  const keys = [...columnsFor].sort();
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
      keys,
    ],
    queryFn: () =>
      lineageApi.tableLineage(ws, catalog, schema, table, {
        direction,
        depth,
        columnsFor: keys,
      }),
    enabled: enabled && !!ws && !!catalog && !!schema && !!table,
    // Expanding a node re-requests the same graph with more detail on it. Holding
    // the previous answer while that is in flight keeps the canvas from blanking
    // out and re-laying itself under the cursor mid-click.
    placeholderData: (previous) => previous,
  });
}
