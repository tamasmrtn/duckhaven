import type { Workspace, WorkspaceMember } from "@/types/workspace";

function makeWorkspaces(): Workspace[] {
  return [
    {
      id: "ws-1",
      slug: "acme-analytics",
      name: "acme-analytics",
      default_catalog: "acme_analytics",
      storage_backend_id: "sb-1",
      storage_backend_kind: "s3",
      created_at: "2026-01-01T00:00:00Z",
    },
    {
      id: "ws-2",
      slug: "acme-research",
      name: "acme-research",
      default_catalog: "acme_research",
      storage_backend_id: "sb-2",
      storage_backend_kind: "adls_gen2",
      created_at: "2026-01-15T00:00:00Z",
    },
    {
      id: "ws-3",
      slug: "public",
      name: "public",
      default_catalog: "public",
      storage_backend_id: "sb-4",
      storage_backend_kind: "object_store",
      created_at: "2026-01-01T00:00:00Z",
    },
    {
      id: "ws-4",
      slug: "home-lab",
      name: "home-lab",
      default_catalog: "home_lab",
      storage_backend_id: "sb-3",
      storage_backend_kind: "object_store",
      created_at: "2026-02-01T00:00:00Z",
    },
  ];
}

// MemberOut (api/schemas/workspace.py) is `{workspace_id, user_id, role}` — no
// email/name. Members keyed by workspace id for handler lookup.
function makeMembers(): Record<string, WorkspaceMember[]> {
  return {
    "ws-1": [
      { workspace_id: "ws-1", user_id: "u-1", role: "owner" },
      { workspace_id: "ws-1", user_id: "u-2", role: "writer" },
      { workspace_id: "ws-1", user_id: "u-3", role: "reader" },
    ],
    "ws-2": [
      { workspace_id: "ws-2", user_id: "u-1", role: "writer" },
      { workspace_id: "ws-2", user_id: "u-2", role: "owner" },
    ],
    "ws-3": [
      { workspace_id: "ws-3", user_id: "u-1", role: "reader" },
      { workspace_id: "ws-3", user_id: "u-2", role: "reader" },
      { workspace_id: "ws-3", user_id: "u-3", role: "reader" },
    ],
    "ws-4": [{ workspace_id: "ws-4", user_id: "u-1", role: "owner" }],
  };
}

export let WORKSPACES = makeWorkspaces();
export let WORKSPACE_MEMBERS = makeMembers();

export function resetWorkspaces(): void {
  WORKSPACES = makeWorkspaces();
  WORKSPACE_MEMBERS = makeMembers();
}

export function findWorkspace(slugOrId: string): Workspace | undefined {
  return WORKSPACES.find((w) => w.slug === slugOrId || w.id === slugOrId);
}

export function userRoleInWorkspace(wsId: string, userId: string) {
  const members = WORKSPACE_MEMBERS[wsId] ?? [];
  return members.find((m) => m.user_id === userId)?.role ?? null;
}
