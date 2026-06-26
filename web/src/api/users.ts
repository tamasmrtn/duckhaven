import { get, patch, post } from "./client";
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

export const usersApi = {
  adminList: () => get<User[]>("/admin/users"),

  create: (input: CreateUserInput) => post<User>("/admin/users", input),

  update: (id: string, input: UpdateUserInput) =>
    patch<User>(`/admin/users/${id}`, input),

  revokeSessions: (id: string) =>
    post<void>(`/admin/users/${id}/revoke-sessions`),
};
