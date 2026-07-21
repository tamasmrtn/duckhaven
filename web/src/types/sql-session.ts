export type SqlSessionStatus =
  "opening" | "open" | "closing" | "closed" | "expired" | "failed";

// Why a session ended. Null while it is still live, and on sessions that ended
// before the server recorded a reason.
export type SessionCloseReason =
  | "client"
  | "idle"
  | "max_lifetime"
  | "open_timeout"
  | "agent_disconnect"
  | "agent_lease"
  | "failed";

export interface SqlSession {
  id: string;
  workspace_id: string;
  status: SqlSessionStatus;
  agent_id: string | null;
  user_id?: string | null;
  active_catalog: string | null;
  // Scoped object-storage prefix a load may COPY to/from (dlt staging).
  staging_uri: string | null;
  error: string | null;
  close_reason?: SessionCloseReason | null;
  // The tool that opened the session, from its User-Agent (`dbt-duckhaven`).
  client_name?: string | null;
  client_version?: string | null;
  created_at: string;
  opened_at?: string | null;
  last_active_at: string;
  closed_at?: string | null;
  // Present on the list endpoint, which joins them so a row renders without a
  // follow-up request per session.
  user_name?: string | null;
  agent_name?: string | null;
  statement_count?: number;
}

// A session still holding its agent's admission slot — the operator view.
export const LIVE_SESSION_STATUSES: SqlSessionStatus[] = [
  "opening",
  "open",
  "closing",
];

export function isLiveSession(session: SqlSession): boolean {
  return LIVE_SESSION_STATUSES.includes(session.status);
}
