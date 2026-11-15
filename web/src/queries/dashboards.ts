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

/** How often a preview's page checks whether its file changed. */
export const PREVIEW_FOLLOW_MS = 2000;

/**
 * A preview, read again every couple of seconds so its page follows the file
 * as `dh dashboard preview` sends each save. It stops once the preview is gone.
 */
export function useDashboardPreview(ws: string, id: string) {
  return useQuery({
    queryKey: ["workspace", ws, "dashboard-preview", id],
    queryFn: () => dashboardsApi.getPreview(ws, id),
    enabled: !!ws && !!id,
    retry: false,
    refetchInterval: (query) =>
      query.state.status === "error" ? false : PREVIEW_FOLLOW_MS,
  });
}

/** Changes the assistant proposed to a dashboard. A 404 means it is gone. */
export function useDashboardProposal(ws: string, id: string) {
  return useQuery({
    queryKey: ["workspace", ws, "dashboard-proposal", id],
    queryFn: () => dashboardsApi.getProposal(ws, id),
    enabled: !!ws && !!id,
    retry: false,
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
