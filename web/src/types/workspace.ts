import type { BackendKind } from "./storage-backend";

export type WorkspaceMemberRole = "owner" | "writer" | "reader";

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
  created_at: string;
}

export interface WorkspaceMember {
  workspace_id: string;
  user_id: string;
  role: WorkspaceMemberRole;
}
