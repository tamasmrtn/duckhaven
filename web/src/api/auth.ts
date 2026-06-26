import { get, post } from "./client";
import type { AuthMethods, User } from "@/types/auth";

export const authApi = {
  login: (email: string, password: string) =>
    post<User>("/auth/login", { email, password }),

  logout: () => post<void>("/auth/logout"),

  me: () => get<User>("/me"),

  methods: () => get<AuthMethods>("/auth/methods"),
};
