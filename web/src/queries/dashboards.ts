import { useQuery } from "@tanstack/react-query";
import { dashboardsApi } from "@/api/dashboards";

// Query keys follow the house shape: hierarchical, alternating label and value,
// most specific last. A run is a stream, not a query; see useDashboardRun.

export function useDashboards(ws: string) {
  return useQuery({
    queryKey: ["workspace", ws, "dashboards"],
    queryFn: () => dashboardsApi.list(ws),
    enabled: !!ws,
  });
}

/** One dashboard: its published file, or with `draft` its draft. */
export function useDashboard(ws: string, name: string, draft = false) {
  return useQuery({
    queryKey: ["workspace", ws, "dashboard", name, ...(draft ? ["draft"] : [])],
    queryFn: () => dashboardsApi.get(ws, name, draft),
    enabled: !!ws && !!name,
    // A refusal, such as a reader asking for a draft, is the answer.
    ...(draft ? { retry: false } : {}),
  });
}

export function useDashboardRevisions(
  ws: string,
  name: string,
  enabled = true,
) {
  return useQuery({
    queryKey: ["workspace", ws, "dashboard", name, "revisions"],
    queryFn: () => dashboardsApi.revisions(ws, name),
    enabled: enabled && !!ws && !!name,
  });
}

/** The JSON Schema of a dashboard file. It changes only with the server. */
export function useDashboardSchema() {
  return useQuery({
    queryKey: ["dashboard-schema", 1],
    queryFn: () => dashboardsApi.schema(),
    staleTime: Infinity,
  });
}
