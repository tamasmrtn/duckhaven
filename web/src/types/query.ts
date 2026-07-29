export type QueryStatus =
  "queued" | "running" | "done" | "failed" | "cancelled";

export interface Query {
  id: string;
  workspace_id: string;
  agent_id: string;
  user_id?: string | null;
  // Display name of who ran the query (resolved from user_id server-side).
  user_name?: string | null;
  sql: string;
  status: QueryStatus;
  // Tags non-interactive runs (e.g. "scheduled"); null/absent for user runs.
  origin?: string | null;
  // Set when the run was produced by a schedule; maps a run to its schedule.
  schedule_id?: string | null;
  // Set when the run is a statement inside a SQL session (origin="session");
  // maps a statement to the workload it belonged to.
  session_id?: string | null;
  row_count: number | null;
  duration_ms: number | null;
  result_bytes: number | null;
  error: string | null;
  progress?: Record<string, unknown> | null;
  started_at: string;
  // When the agent admitted the run and started executing it. With started_at
  // (submission) this splits the wall-clock into queue wait and execution. Null
  // for a run that never started or one recorded before the column existed.
  running_at?: string | null;
  finished_at: string | null;
}

export interface QueryProfileSummary {
  latency_ms: number;
  cpu_time_ms: number;
  rows_returned: number;
  result_bytes: number;
  peak_memory_bytes: number;
  spill_bytes: number;
  bytes_read: number;
  bytes_written: number;
  // The admission reservation the query ran under. Optional: profiles captured
  // before this was recorded omit them.
  reserved_memory_bytes?: number;
  reserved_threads?: number;
}

export interface QueryProfileNode {
  type: string;
  name: string;
  estimated_cardinality: number | null;
  rows_scanned: number | null;
  rows_produced: number | null;
  time_ms: number | null;
  result_bytes: number | null;
  extra_info: Record<string, unknown>;
  children: QueryProfileNode[];
}

export interface QueryProfile {
  summary: QueryProfileSummary;
  tree: QueryProfileNode;
}

export interface QueryRow {
  [column: string]: string | number | boolean | null;
}

export interface QueryRowsPage {
  rows: QueryRow[];
  columns: string[];
  cursor: string | null;
  total: number;
}
