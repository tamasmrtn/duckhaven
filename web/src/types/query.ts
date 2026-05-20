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
  error: string | null;
  started_at: string;
  finished_at: string | null;
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
