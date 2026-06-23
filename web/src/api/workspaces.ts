import { get, post } from "./client";
import type { Workspace, WorkspaceMember } from "@/types/workspace";

export const workspacesApi = {
  list: () => get<Workspace[]>("/workspaces"),

  get: (ws: string) => get<Workspace>(`/workspaces/${ws}`),

  // A new workspace starts with no catalog; storage is chosen per catalog.
  create: (data: { slug: string; name: string }) =>
    post<Workspace>("/workspaces", data),

  members: (ws: string) => get<WorkspaceMember[]>(`/workspaces/${ws}/members`),
};
