import { http, HttpResponse } from "msw";
import {
  WORKSPACES,
  WORKSPACE_MEMBERS,
  findWorkspace,
} from "../fixtures/workspaces";
import { STORAGE_BACKENDS } from "../fixtures/storage-backends";
import { nextId } from "../lib/seed";
import { httpError } from "../lib/errors";
import type { WorkspaceMemberRole } from "@/types/workspace";

export const workspaceHandlers = [
  http.get("/api/workspaces", () => {
    return HttpResponse.json(WORKSPACES);
  }),

  http.post("/api/workspaces", async ({ request }) => {
    const body = (await request.json()) as {
      slug: string;
      name: string;
      storage_backend_id?: string;
    };
    let backend;
    if (body.storage_backend_id == null) {
      // Mirror the API: auto-provision a bundled object-store backend.
      backend = {
        id: nextId("sb"),
        kind: "object_store" as const,
        name: body.name,
        root_uri: "",
        uc_storage_credential_id: null,
        uc_credential_valid: null,
        workspace_count: 0,
        created_by: "u-1",
        created_at: new Date().toISOString(),
      };
      STORAGE_BACKENDS.push(backend);
    } else {
      backend = STORAGE_BACKENDS.find((b) => b.id === body.storage_backend_id);
      if (!backend) return httpError(404, "Storage backend not found");
    }
    const ws = {
      id: nextId("ws"),
      slug: body.slug,
      name: body.name,
      storage_backend_id: backend.id,
      storage_backend_kind: backend.kind,
      created_at: new Date().toISOString(),
    };
    WORKSPACES.push(ws);
    return HttpResponse.json(ws, { status: 201 });
  }),

  http.get("/api/workspaces/:ws", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    return HttpResponse.json(ws);
  }),

  http.get("/api/workspaces/:ws/members", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    return HttpResponse.json(WORKSPACE_MEMBERS[ws.id] ?? []);
  }),

  http.post("/api/workspaces/:ws/members", async ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const body = (await request.json()) as {
      user_id: string;
      role?: WorkspaceMemberRole;
    };
    const member = {
      workspace_id: ws.id,
      user_id: body.user_id,
      role: body.role ?? "reader",
    };
    const members = WORKSPACE_MEMBERS[ws.id] ?? (WORKSPACE_MEMBERS[ws.id] = []);
    members.push(member);
    return HttpResponse.json(member, { status: 201 });
  }),
];
