import type { Schedule } from "@/types/schedule";
import type { Query } from "@/types/query";

function makeSchedules(): Schedule[] {
  return [
    {
      id: "sch-1",
      workspace_id: "ws-1",
      job_type: "saved_query",
      saved_query_id: "sq-1",
      agent_id: "ag-1",
      cron: "0 2 * * *",
      enabled: true,
      next_run_at: "2026-06-30T02:00:00Z",
      last_run_at: "2026-06-29T02:00:00Z",
      last_run_query_id: "schq-1",
      created_at: "2026-06-01T00:00:00Z",
    },
  ];
}

// Past runs of sch-1, newest first — surfaced by the per-schedule run history.
function makeScheduleRuns(): Query[] {
  return [
    {
      id: "schq-1",
      workspace_id: "ws-1",
      agent_id: "ag-1",
      user_id: null,
      sql: "SELECT date_trunc('day', event_time) d, count(*) n FROM raw.events GROUP BY 1",
      status: "done",
      origin: "scheduled",
      row_count: 30,
      duration_ms: 1400,
      result_bytes: 4096,
      error: null,
      progress: null,
      started_at: "2026-06-29T02:00:00Z",
      finished_at: "2026-06-29T02:00:01.4Z",
    },
    {
      id: "schq-2",
      workspace_id: "ws-1",
      agent_id: "ag-1",
      user_id: null,
      sql: "SELECT date_trunc('day', event_time) d, count(*) n FROM raw.events GROUP BY 1",
      status: "failed",
      origin: "scheduled",
      row_count: null,
      duration_ms: null,
      result_bytes: null,
      error: "Configured agent is not connected",
      progress: null,
      started_at: "2026-06-28T02:00:00Z",
      finished_at: "2026-06-28T02:00:00Z",
    },
  ];
}

export let SCHEDULES = makeSchedules();
export let SCHEDULE_RUNS = makeScheduleRuns();

export function resetSchedules(): void {
  SCHEDULES = makeSchedules();
  SCHEDULE_RUNS = makeScheduleRuns();
}
