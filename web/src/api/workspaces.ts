import { get, post, patch, del } from "./client";
import type { Workspace, WorkspaceMember } from "@/types/workspace";

export const workspacesApi = {
  list: () => get<Workspace[]>("/workspaces"),

  get: (ws: string) => get<Workspace>(`/workspaces/${ws}`),

  // A new workspace starts with no catalog; storage is chosen per catalog.
  create: (data: { slug: string; name: string }) =>
    post<Workspace>("/workspaces", data),

  members: (ws: string) => get<WorkspaceMember[]>(`/workspaces/${ws}/members`),

  // Slug is not renameable — it is the routable /$ws/... segment.
  update: (ws: string, data: { name?: string; description?: string }) =>
    patch<Workspace>(`/workspaces/${ws}`, data),

  remove: (ws: string) => del(`/workspaces/${ws}`),
};
