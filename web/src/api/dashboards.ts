import { get, getAllPages } from "./client";
import { postSSE } from "./sse";
import type {
  Dashboard,
  DashboardRunRequest,
  DashboardSummary,
  RunEvent,
} from "@/types/dashboard";

const base = (ws: string) => `/workspaces/${ws}/dashboards`;

export const dashboardsApi = {
  list: (ws: string) => getAllPages<DashboardSummary>(base(ws)),

  get: (ws: string, name: string) =>
    get<Dashboard>(`${base(ws)}/${encodeURIComponent(name)}`),

  /**
   * View a dashboard as the signed-in person: yields `start`, a `widget` event
   * per widget as it is answered, and `done`. Aborting `signal` closes the
   * stream, and the server cancels the queries still running.
   */
  run: (
    ws: string,
    name: string,
    body: DashboardRunRequest,
    signal?: AbortSignal,
  ) =>
    postSSE<RunEvent>(
      `${base(ws)}/${encodeURIComponent(name)}/run`,
      body,
      signal,
    ),
};
