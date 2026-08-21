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
  // Coarse kind of statement ("select", "insert", …), classified when the run
  // was recorded. Null/absent means unknown — the statement did not parse, or
  // the row predates classification. Not the same as "other", which means the
  // statement parsed and nothing more specific fit.
  statement_type?: string | null;
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
  // Memory this statement itself allocated. peak/spill above come from DuckDB
  // counters that are high-water marks for the whole connection, so on a held
  // session they read as "how much this statement raised the bar" and are 0 for
  // one that stayed under an earlier peak. Optional: older profiles omit it.
  memory_allocated_bytes?: number;
  // The admission reservation the query ran under. Optional: profiles captured
  // before this was recorded omit them.
  reserved_memory_bytes?: number;
  reserved_threads?: number;
  // How long the statement waited for that reservation before it could start.
  // DuckHaven's own measurement, not DuckDB's.
  admission_wait_ms?: number;
  // DuckDB's BLOCKED_THREAD_TIME: time threads spent parked waiting on I/O or
  // on another operator rather than working. Optional: older profiles omit it.
  //
  // These three do not partition latency and must not be presented as if they
  // did — cpu_time sums across threads and can exceed wall clock, and blocked
  // time overlaps with it.
  blocked_thread_time_ms?: number;
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

/** One page of query history.
 *
 * Carries no total on purpose: counting the rows behind a page costs a second
 * aggregate over the same predicates on every request, to show a number that is
 * stale as soon as anyone runs a query. `has_more` is free and lets the UI say
 * what it actually knows.
 */
export interface QueriesPage {
  items: Query[];
  // Opaque; feed back as `cursor` for the next page. Null on the last page.
  cursor: string | null;
  has_more: boolean;
}

export interface QueryRow {
  [column: string]: string | number | boolean | null;
}

export interface ColumnSchema {
  name: string;
  type: string;
}

export interface QueryRowsPage {
  rows: QueryRow[];
  columns: string[];
  cursor: string | null;
  total: number;
  column_schema?: ColumnSchema[] | null;
}
