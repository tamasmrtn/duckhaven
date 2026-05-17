import { get, post } from "./client";
import type { Workspace, WorkspaceMember } from "@/types/workspace";
import type { BackendKind } from "@/types/storage-backend";

export const workspacesApi = {
  list: () => get<Workspace[]>("/workspaces"),

  get: (ws: string) => get<Workspace>(`/workspaces/${ws}`),

  create: (data: {
    slug: string;
    name: string;
    storage_backend_id: string;
    kind: BackendKind;
  }) => post<Workspace>("/workspaces", data),

  members: (ws: string) => get<WorkspaceMember[]>(`/workspaces/${ws}/members`),
};
