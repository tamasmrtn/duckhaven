import type { Workspace, WorkspaceMember } from "@/types/workspace";

export const WORKSPACES: Workspace[] = [
  {
    id: "ws-1",
    slug: "acme-analytics",
    name: "acme-analytics",
    storage_backend_id: "sb-1",
    storage_backend_kind: "s3",
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "ws-2",
    slug: "acme-research",
    name: "acme-research",
    storage_backend_id: "sb-2",
    storage_backend_kind: "adls_gen2",
    created_at: "2026-01-15T00:00:00Z",
  },
  {
    id: "ws-3",
    slug: "public",
    name: "public",
    storage_backend_id: "sb-4",
    storage_backend_kind: "local_fs",
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "ws-4",
    slug: "home-lab",
    name: "home-lab",
    storage_backend_id: "sb-3",
    storage_backend_kind: "nas",
    created_at: "2026-02-01T00:00:00Z",
  },
];

export const WORKSPACE_MEMBERS: Record<string, WorkspaceMember[]> = {
  "ws-1": [
    {
      user_id: "u-1",
      email: "marton@duckhaven.local",
      name: "Marton",
      role: "owner",
    },
    {
      user_id: "u-2",
      email: "jess@duckhaven.local",
      name: "Jess",
      role: "writer",
    },
    {
      user_id: "u-3",
      email: "alex@duckhaven.local",
      name: "Alex",
      role: "reader",
    },
  ],
  "ws-2": [
    {
      user_id: "u-1",
      email: "marton@duckhaven.local",
      name: "Marton",
      role: "writer",
    },
    {
      user_id: "u-2",
      email: "jess@duckhaven.local",
      name: "Jess",
      role: "owner",
    },
  ],
  "ws-3": [
    {
      user_id: "u-1",
      email: "marton@duckhaven.local",
      name: "Marton",
      role: "reader",
    },
    {
      user_id: "u-2",
      email: "jess@duckhaven.local",
      name: "Jess",
      role: "reader",
    },
    {
      user_id: "u-3",
      email: "alex@duckhaven.local",
      name: "Alex",
      role: "reader",
    },
  ],
  "ws-4": [
    {
      user_id: "u-1",
      email: "marton@duckhaven.local",
      name: "Marton",
      role: "owner",
    },
  ],
};

export function findWorkspace(slugOrId: string): Workspace | undefined {
  return WORKSPACES.find((w) => w.slug === slugOrId || w.id === slugOrId);
}

export function userRoleInWorkspace(wsId: string, userId: string) {
  const members = WORKSPACE_MEMBERS[wsId] ?? [];
  return members.find((m) => m.user_id === userId)?.role ?? null;
}
