import { get, patch, post } from "./client";
import type { AuthMethods, NavMode, Theme, User } from "@/types/auth";

export const authApi = {
  login: (email: string, password: string) =>
    post<User>("/auth/login", { email, password }),

  logout: () => post<void>("/auth/logout"),

  me: () => get<User>("/me"),

  /** Change the caller's own preferences; only the fields sent change. */
  updateMe: (body: { theme?: Theme | null; sidebar?: NavMode | null }) =>
    patch<User>("/me", body),

  methods: () => get<AuthMethods>("/auth/methods"),
};
