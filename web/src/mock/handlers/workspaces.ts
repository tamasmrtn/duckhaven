import { http, HttpResponse } from "msw";
import {
  WORKSPACES,
  WORKSPACE_MEMBERS,
  findWorkspace,
} from "../fixtures/workspaces";

export const workspaceHandlers = [
  http.get("/api/workspaces", () => {
    return HttpResponse.json(WORKSPACES);
  }),

  http.post("/api/workspaces", async ({ request }) => {
    const body = (await request.json()) as {
      slug: string;
      name: string;
      storage_backend_id: string;
    };
    const ws = {
      id: `ws-${Date.now()}`,
      slug: body.slug,
      name: body.name,
      storage_backend_id: body.storage_backend_id,
      storage_backend_kind: "local_fs" as const,
      created_at: new Date().toISOString(),
    };
    WORKSPACES.push(ws);
    return HttpResponse.json(ws, { status: 201 });
  }),

  http.get("/api/workspaces/:ws", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(ws);
  }),

  http.get("/api/workspaces/:ws/members", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(WORKSPACE_MEMBERS[ws.id] ?? []);
  }),
];
