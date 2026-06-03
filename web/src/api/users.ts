import { get } from "./client";
import type { User } from "@/types/auth";

export const usersApi = {
  adminList: () => get<User[]>("/admin/users"),
};
