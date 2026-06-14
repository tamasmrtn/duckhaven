export type QueryStatus =
  | "queued"
  | "running"
  | "done"
  | "failed"
  | "cancelled";

export interface Query {
  id: string;
  workspace_id: string;
  agent_id: string;
  user_id?: string | null;
  sql: string;
  status: QueryStatus;
  row_count: number | null;
  duration_ms: number | null;
  result_bytes: number | null;
  error: string | null;
  progress?: Record<string, unknown> | null;
  started_at: string;
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
