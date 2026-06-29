// A cron-triggered job. Generic by design (job_type discriminates the work);
// v1 only runs saved queries. Mirrors api/schemas/query.py::ScheduleOut.
export interface Schedule {
  id: string;
  workspace_id: string;
  job_type: string;
  saved_query_id: string | null;
  agent_id: string | null;
  cron: string;
  enabled: boolean;
  next_run_at: string | null;
  last_run_at: string | null;
  last_run_query_id: string | null;
  created_at: string;
}
