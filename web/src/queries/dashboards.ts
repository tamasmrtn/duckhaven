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

export function useDashboard(ws: string, name: string) {
  return useQuery({
    queryKey: ["workspace", ws, "dashboard", name],
    queryFn: () => dashboardsApi.get(ws, name),
    enabled: !!ws && !!name,
  });
}
