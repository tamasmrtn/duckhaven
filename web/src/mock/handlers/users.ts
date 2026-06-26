import { http, HttpResponse } from "msw";
import { ALL_USERS } from "../fixtures/users";
import type { User } from "@/types/auth";

export const userHandlers = [
  http.get("/api/admin/users", () => {
    return HttpResponse.json(ALL_USERS);
  }),

  http.post("/api/admin/users", async ({ request }) => {
    const body = (await request.json()) as Partial<User>;
    return HttpResponse.json(
      {
        id: "u-new",
        email: body.email,
        name: body.name,
        role: body.role ?? "user",
        theme: "system",
        auth_provider: "local",
        is_active: true,
        created_at: "2026-06-26T00:00:00Z",
      },
      { status: 201 },
    );
  }),

  http.patch("/api/admin/users/:id", async ({ params, request }) => {
    const body = (await request.json()) as Partial<User>;
    const base = ALL_USERS.find((u) => u.id === params.id) ?? ALL_USERS[1];
    return HttpResponse.json({ ...base, ...body });
  }),

  http.post("/api/admin/users/:id/revoke-sessions", () => {
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("/api/admin/users/:id/workspaces", () => {
    return HttpResponse.json([
      {
        workspace_id: "w-1",
        slug: "analytics",
        name: "Analytics",
        role: "reader",
      },
      { workspace_id: "w-2", slug: "finance", name: "Finance", role: null },
    ]);
  }),

  http.put(
    "/api/admin/users/:id/workspaces/:ws",
    async ({ params, request }) => {
      const body = (await request.json()) as { role: string };
      return HttpResponse.json({
        workspace_id: "w-x",
        slug: params.ws,
        name: String(params.ws),
        role: body.role,
      });
    },
  ),

  http.delete("/api/admin/users/:id/workspaces/:ws", () => {
    return new HttpResponse(null, { status: 204 });
  }),
];
