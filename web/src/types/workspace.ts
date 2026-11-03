import type { BackendKind } from "./storage-backend";

export type WorkspaceMemberRole = "owner" | "writer" | "reader";

/** The compute a workspace runs on when nothing names an agent. */
export interface DefaultAgent {
  id: string;
  name: string;
  provider: string | null;
  lifecycle: string | null;
  status: string;
}

export interface Workspace {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  // Storage lives on catalogs now; these summarize the workspace's default
  // catalog and are null when the workspace has no catalog attached yet.
  default_catalog: string | null;
  storage_backend_id: string | null;
  storage_backend_kind: BackendKind | null;
  // Whether repeated queries may be answered from the result cache.
  result_cache_enabled?: boolean;
  // Every member can run on this agent whether or not a grant reaches them.
  default_agent_id?: string | null;
  default_agent?: DefaultAgent | null;
  created_at: string;
}

export interface WorkspaceMember {
  workspace_id: string;
  user_id: string;
  role: WorkspaceMemberRole;
}
