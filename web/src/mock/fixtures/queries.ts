import type { Query } from "@/types/query";
import type { SavedQuery } from "@/types/saved-query";

// Terminal queries surfaced by History + Audit. Each carries user_id + progress
// to mirror QueryOut (api/schemas/query.py).
function makeQueryHistory(): Query[] {
  return [
    {
      id: "q-1",
      workspace_id: "ws-1",
      agent_id: "ag-1",
      user_id: "u-1",
      sql: "SELECT date_trunc('day', event_time) d, count(*) n FROM raw.events WHERE event_time >= '2026-05-01' GROUP BY 1 ORDER BY 1",
      status: "done",
      row_count: 30,
      duration_ms: 1400,
      result_bytes: 4096,
      error: null,
      progress: null,
      started_at: "2026-05-15T10:00:00Z",
      finished_at: "2026-05-15T10:00:01.4Z",
    },
    {
      id: "q-2",
      workspace_id: "ws-1",
      agent_id: "ag-2",
      user_id: "u-2",
      sql: "SELECT * FROM raw.users LIMIT 100",
      status: "done",
      row_count: 100,
      duration_ms: 320,
      result_bytes: 12288,
      error: null,
      progress: null,
      started_at: "2026-05-15T09:30:00Z",
      finished_at: "2026-05-15T09:30:00.32Z",
    },
    {
      id: "q-3",
      workspace_id: "ws-1",
      agent_id: "ag-1",
      user_id: "u-1",
      sql: "SELECT * FROM raw.events CROSS JOIN raw.page_views LIMIT 1000000",
      status: "failed",
      row_count: null,
      duration_ms: 5200,
      result_bytes: null,
      error: "Query exceeded memory limit (6 GB)",
      progress: null,
      started_at: "2026-05-14T16:00:00Z",
      finished_at: "2026-05-14T16:00:05.2Z",
    },
    {
      id: "q-4",
      workspace_id: "ws-1",
      agent_id: "ag-1",
      user_id: "u-3",
      sql: "SELECT step, users, pct FROM analytics.funnel ORDER BY users DESC",
      status: "done",
      row_count: 12,
      duration_ms: 88,
      result_bytes: 2048,
      error: null,
      progress: null,
      started_at: "2026-05-14T14:00:00Z",
      finished_at: "2026-05-14T14:00:00.088Z",
    },
    {
      id: "q-5",
      workspace_id: "ws-2",
      agent_id: "ag-2",
      user_id: "u-2",
      sql: "SELECT variant, count(*) FROM experiments.ab_assignments WHERE assigned_at >= '2026-05-01' GROUP BY 1",
      status: "cancelled",
      row_count: null,
      duration_ms: 2100,
      result_bytes: null,
      error: null,
      progress: null,
      started_at: "2026-05-13T11:00:00Z",
      finished_at: "2026-05-13T11:00:02.1Z",
    },
  ];
}

// created_by is a user id (SavedQueryOut.created_by: uuid), not an email.
function makeSavedQueries(): SavedQuery[] {
  return [
    {
      id: "sq-1",
      name: "Daily events",
      sql: "SELECT date_trunc('day', event_time) d, count(*) n FROM raw.events WHERE event_time >= '2026-05-01' GROUP BY 1 ORDER BY 1",
      workspace_id: "ws-1",
      default_agent_id: "ag-1",
      created_by: "u-1",
      created_at: "2026-05-01T00:00:00Z",
      last_run_at: "2026-05-15T10:00:00Z",
    },
    {
      id: "sq-2",
      name: "Funnel overview",
      sql: "SELECT step, users, pct FROM analytics.funnel ORDER BY users DESC",
      workspace_id: "ws-1",
      default_agent_id: "ag-1",
      created_by: "u-1",
      created_at: "2026-04-20T00:00:00Z",
      last_run_at: "2026-05-14T14:00:00Z",
    },
    {
      id: "sq-3",
      name: "AB experiment counts",
      sql: "SELECT variant, count(*) FROM experiments.ab_assignments WHERE assigned_at >= '2026-05-01' GROUP BY 1",
      workspace_id: "ws-2",
      default_agent_id: "ag-2",
      created_by: "u-2",
      created_at: "2026-05-10T00:00:00Z",
      last_run_at: "2026-05-13T11:00:00Z",
    },
  ];
}

export let QUERY_HISTORY = makeQueryHistory();
export let SAVED_QUERIES = makeSavedQueries();

export function resetQueries(): void {
  QUERY_HISTORY = makeQueryHistory();
  SAVED_QUERIES = makeSavedQueries();
}
