import { del, get, patch, post, put } from "./client";
import type { User } from "@/types/auth";

export interface CreateUserInput {
  email: string;
  name: string;
  password: string;
  role: string;
}

export interface UpdateUserInput {
  role?: string;
  is_active?: boolean;
}

export interface UserWorkspace {
  workspace_id: string;
  slug: string;
  name: string;
  role: string | null;
}

export const usersApi = {
  adminList: () => get<User[]>("/admin/users"),

  create: (input: CreateUserInput) => post<User>("/admin/users", input),

  update: (id: string, input: UpdateUserInput) =>
    patch<User>(`/admin/users/${id}`, input),

  revokeSessions: (id: string) =>
    post<void>(`/admin/users/${id}/revoke-sessions`),

  workspaces: (id: string) =>
    get<UserWorkspace[]>(`/admin/users/${id}/workspaces`),

  setWorkspaceRole: (id: string, ws: string, role: string) =>
    put<UserWorkspace>(`/admin/users/${id}/workspaces/${ws}`, { role }),

  removeWorkspace: (id: string, ws: string) =>
    del(`/admin/users/${id}/workspaces/${ws}`),
};
