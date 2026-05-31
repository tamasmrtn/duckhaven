import type { BackendKind } from "./storage-backend";

export type WorkspaceMemberRole = "owner" | "writer" | "reader";

export interface Workspace {
  id: string;
  slug: string;
  name: string;
  storage_backend_id: string;
  storage_backend_kind: BackendKind;
  created_at: string;
}

export interface WorkspaceMember {
  workspace_id: string;
  user_id: string;
  role: WorkspaceMemberRole;
}
