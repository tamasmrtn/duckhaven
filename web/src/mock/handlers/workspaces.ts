import { http, HttpResponse } from "msw";
import {
  WORKSPACES,
  WORKSPACE_MEMBERS,
  findWorkspace,
  removeWorkspace,
} from "../fixtures/workspaces";
import { nextId } from "../lib/seed";
import { httpError } from "../lib/errors";
import type { WorkspaceMemberRole } from "@/types/workspace";

export const workspaceHandlers = [
  http.get("/api/workspaces", () => {
    return HttpResponse.json(WORKSPACES);
  }),

  http.post("/api/workspaces", async ({ request }) => {
    const body = (await request.json()) as { slug: string; name: string };
    // Mirror the API: a new workspace starts with no catalog and no storage.
    const ws = {
      id: nextId("ws"),
      slug: body.slug,
      name: body.name,
      description: null,
      default_catalog: null,
      storage_backend_id: null,
      storage_backend_kind: null,
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

  http.patch("/api/workspaces/:ws", async ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const body = (await request.json()) as {
      name?: string;
      description?: string;
    };
    if (body.name != null) ws.name = body.name;
    if (body.description != null) ws.description = body.description;
    return HttpResponse.json(ws);
  }),

  http.delete("/api/workspaces/:ws", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    removeWorkspace(ws.id);
    return new HttpResponse(null, { status: 204 });
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
