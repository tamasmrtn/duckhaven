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
  user_id: string;
  email: string;
  name: string;
  role: WorkspaceMemberRole;
}
