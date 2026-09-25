export interface SavedQuery {
  id: string;
  name: string;
  sql: string;
  workspace_id: string;
  default_agent_id: string | null | undefined;
  created_by: string;
  created_by_name?: string | null;
  created_at: string;
  // The last change to its SQL, name or default agent, and who made it. The
  // editor is the principal a scheduled run executes as.
  updated_at: string;
  updated_by: string;
  updated_by_name?: string | null;
  last_run_at: string | null | undefined;
}
