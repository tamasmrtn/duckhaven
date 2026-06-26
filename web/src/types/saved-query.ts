export interface SavedQuery {
  id: string;
  name: string;
  sql: string;
  workspace_id: string;
  default_agent_id: string | null | undefined;
  created_by: string;
  created_by_name?: string | null;
  created_at: string;
  last_run_at: string | null | undefined;
}
