import { http, HttpResponse } from "msw";
import { SA_PATS, SERVICE_ACCOUNTS } from "../fixtures/service-accounts";
import { nextId, nextServiceAccountToken } from "../lib/seed";
import { httpError } from "../lib/errors";

function slugify(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "service-account"
  );
}

export const serviceAccountHandlers = [
  http.get("/api/admin/service-accounts", () => {
    return HttpResponse.json(SERVICE_ACCOUNTS);
  }),

  http.post("/api/admin/service-accounts", async ({ request }) => {
    const body = (await request.json()) as { name: string; role?: string };
    const email = `${slugify(body.name)}@service-account.local`;
    if (SERVICE_ACCOUNTS.some((a) => a.email === email)) {
      return httpError(409, "A service account with this name already exists.");
    }
    const sa = {
      id: nextId("sa"),
      name: body.name,
      email,
      role: body.role ?? "user",
      is_active: true,
      created_at: new Date().toISOString(),
      pat_count: 0,
    };
    SERVICE_ACCOUNTS.push(sa);
    SA_PATS[sa.id] = [];
    return HttpResponse.json(sa, { status: 201 });
  }),

  http.patch("/api/admin/service-accounts/:id", async ({ params, request }) => {
    const sa = SERVICE_ACCOUNTS.find((a) => a.id === params.id);
    if (!sa) return httpError(404, "Service account not found");
    const body = (await request.json()) as {
      role?: string;
      is_active?: boolean;
    };
    if (body.role !== undefined) sa.role = body.role;
    if (body.is_active !== undefined) sa.is_active = body.is_active;
    return HttpResponse.json(sa);
  }),

  http.delete("/api/admin/service-accounts/:id", ({ params }) => {
    const idx = SERVICE_ACCOUNTS.findIndex((a) => a.id === params.id);
    if (idx === -1) return httpError(404, "Service account not found");
    SERVICE_ACCOUNTS.splice(idx, 1);
    delete SA_PATS[params.id as string];
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("/api/admin/service-accounts/:id/pats", ({ params }) => {
    const sa = SERVICE_ACCOUNTS.find((a) => a.id === params.id);
    if (!sa) return httpError(404, "Service account not found");
    return HttpResponse.json(SA_PATS[params.id as string] ?? []);
  }),

  http.post(
    "/api/admin/service-accounts/:id/pat",
    async ({ params, request }) => {
      const sa = SERVICE_ACCOUNTS.find((a) => a.id === params.id);
      if (!sa) return httpError(404, "Service account not found");
      const body = (await request.json().catch(() => ({}))) as {
        expires_in_days?: number | null;
      };
      const days =
        body.expires_in_days === undefined ? 90 : body.expires_in_days;
      const expires_at =
        days === null
          ? null
          : new Date(Date.now() + days * 86400000).toISOString();
      const pat = {
        id: nextId("pat"),
        created_at: new Date().toISOString(),
        expires_at,
      };
      SA_PATS[sa.id] = [...(SA_PATS[sa.id] ?? []), pat];
      sa.pat_count = SA_PATS[sa.id].length;
      return HttpResponse.json(
        { id: pat.id, token: nextServiceAccountToken(), expires_at },
        { status: 201 },
      );
    },
  ),

  http.delete("/api/admin/service-accounts/:id/pat/:patId", ({ params }) => {
    const sa = SERVICE_ACCOUNTS.find((a) => a.id === params.id);
    if (!sa) return httpError(404, "Service account not found");
    const list = SA_PATS[params.id as string] ?? [];
    const idx = list.findIndex((p) => p.id === params.patId);
    if (idx === -1) return httpError(404, "PAT not found");
    list.splice(idx, 1);
    sa.pat_count = list.length;
    return new HttpResponse(null, { status: 204 });
  }),
];
