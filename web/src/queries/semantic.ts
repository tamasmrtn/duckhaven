import { useQuery } from "@tanstack/react-query";
import { semanticApi } from "@/api/semantic";

// Query keys follow the house shape: hierarchical, alternating label and value,
// most specific last. Every parameter that changes the response is in the key.

export function useSemanticModels(ws: string, status?: string) {
  return useQuery({
    queryKey: ["workspace", ws, "semantic", "models", status ?? "all"],
    queryFn: () => semanticApi.listModels(ws, status),
    enabled: !!ws,
  });
}

export function useSemanticModel(ws: string, slug: string, enabled = true) {
  return useQuery({
    queryKey: ["workspace", ws, "semantic", "model", slug],
    queryFn: () => semanticApi.getModel(ws, slug),
    enabled: enabled && !!ws && !!slug,
  });
}

/**
 * Which dimensions a given metric can legally be sliced by.
 *
 * Powers the dimension picker, so the UI offers only combinations that will
 * actually compile rather than letting somebody build a query the server then
 * refuses.
 */
export function useMetricDimensions(
  ws: string,
  slug: string,
  metric: string,
  enabled = true,
) {
  return useQuery({
    queryKey: [
      "workspace",
      ws,
      "semantic",
      "model",
      slug,
      "metric",
      metric,
      "dimensions",
    ],
    queryFn: () => semanticApi.metricDimensions(ws, slug, metric),
    enabled: enabled && !!ws && !!slug && !!metric,
  });
}

/**
 * Which semantic definitions depend on one physical table.
 *
 * The direction lineage cannot answer — and the question worth asking just
 * before dropping a column.
 */
export function useTableSemantics(
  ws: string,
  catalog: string,
  schema: string,
  table: string,
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
      "semantic",
    ],
    queryFn: () => semanticApi.tableSemantics(ws, catalog, schema, table),
    enabled: enabled && !!ws && !!catalog && !!schema && !!table,
  });
}
