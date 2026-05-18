import type { User } from "@/types/auth";

export const CURRENT_USER: User = {
  id: "u-1",
  email: "marton@duckhaven.local",
  name: "Marton",
  role: "admin",
  theme: "system",
  created_at: "2026-01-01T00:00:00Z",
};

export const ALL_USERS: User[] = [
  CURRENT_USER,
  {
    id: "u-2",
    email: "jess@duckhaven.local",
    name: "Jess",
    role: "user",
    theme: "dark",
    created_at: "2026-01-15T00:00:00Z",
  },
  {
    id: "u-3",
    email: "alex@duckhaven.local",
    name: "Alex",
    role: "user",
    theme: "light",
    created_at: "2026-02-01T00:00:00Z",
  },
];
