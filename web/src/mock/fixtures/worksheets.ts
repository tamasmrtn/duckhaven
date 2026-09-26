import type { Worksheet } from "@/types/worksheet";

// The showcase workspace opens on two tabs: a plain draft and a draft linked to
// the "Funnel overview" saved query whose SQL has since diverged, so the
// unsaved-changes dot has something to show. Every other workspace starts with
// no worksheets and the page creates the first one.
function makeWorksheets(): Worksheet[] {
  return [
    {
      id: "wk-1",
      workspace_id: "ws-1",
      owner_id: "u-1",
      title: "events.sql",
      sql: `SELECT
  date_trunc('day', event_time) d,
  count(*) n
FROM raw.events
WHERE event_time >= '2026-05-01'
GROUP BY 1
ORDER BY 1;`,
      agent_id: null,
      catalog: null,
      timeout_s: null,
      saved_query_id: null,
      last_query_id: null,
      is_open: true,
      tab_position: 0,
      version: 1,
      created_at: "2026-05-15T09:00:00Z",
      updated_at: "2026-05-15T09:00:00Z",
    },
    {
      id: "wk-2",
      workspace_id: "ws-1",
      owner_id: "u-1",
      title: "funnel-draft",
      sql: "SELECT step, users, pct FROM analytics.funnel ORDER BY users DESC LIMIT 10",
      agent_id: null,
      catalog: null,
      timeout_s: null,
      saved_query_id: "sq-2",
      last_query_id: null,
      is_open: true,
      tab_position: 1,
      version: 3,
      created_at: "2026-05-14T09:00:00Z",
      updated_at: "2026-05-15T08:00:00Z",
    },
    {
      id: "wk-3",
      workspace_id: "ws-1",
      owner_id: "u-1",
      title: "retention scratch",
      sql: "SELECT cohort, count(*) FROM analytics.retention GROUP BY 1",
      agent_id: null,
      catalog: null,
      timeout_s: null,
      saved_query_id: null,
      last_query_id: null,
      is_open: false,
      tab_position: 0,
      version: 2,
      created_at: "2026-05-10T09:00:00Z",
      updated_at: "2026-05-12T09:00:00Z",
    },
  ];
}

export let WORKSHEETS: Worksheet[] = makeWorksheets();

export function resetWorksheets(): void {
  WORKSHEETS = makeWorksheets();
}
