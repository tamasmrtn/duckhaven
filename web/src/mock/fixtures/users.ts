import type { User } from "@/types/auth";

export const CURRENT_USER: User = {
  id: "u-1",
  email: "marton@duckhaven.local",
  name: "Marton",
  role: "admin",
  theme: "system",
  auth_provider: "local",
  is_active: true,
  permissions: [
    "agents:manage",
    "storage:manage",
    "users:manage",
    "maintenance:manage",
    "catalogs:admin",
    "queries:admin",
  ],
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
    auth_provider: "local",
    is_active: true,
    created_at: "2026-01-15T00:00:00Z",
  },
  {
    id: "u-3",
    email: "alex@duckhaven.local",
    name: "Alex",
    role: "user",
    theme: "light",
    auth_provider: "oidc",
    is_active: true,
    created_at: "2026-02-01T00:00:00Z",
  },
];
