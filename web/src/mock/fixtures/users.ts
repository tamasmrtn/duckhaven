import type { User } from "@/types/auth";

export const CURRENT_USER: User = {
  id: "u-1",
  email: "marton@duckhaven.local",
  name: "Marton",
  role: "admin",
  theme: "system",
  sidebar: null,
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

// What PATCH /api/me has changed on the signed-in user since the last reset.
let currentUserChanges: Partial<User> = {};

/** The signed-in user as `/api/me` answers: the fixture plus saved preferences. */
export function currentUser(): User {
  return { ...CURRENT_USER, ...currentUserChanges };
}

export function updateCurrentUser(changes: Partial<User>): User {
  currentUserChanges = { ...currentUserChanges, ...changes };
  return currentUser();
}

export function resetUsers(): void {
  currentUserChanges = {};
}

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
