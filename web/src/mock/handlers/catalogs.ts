import { http, HttpResponse } from "msw";
import {
  CATALOGS,
  catalogsForWorkspace,
  findCatalogById,
} from "../fixtures/catalogs";
import { findWorkspace } from "../fixtures/workspaces";
import { httpError } from "../lib/errors";
import type { Catalog } from "@/types/catalog";

function out(c: (typeof CATALOGS)[number]): Catalog {
  return {
    id: c.id,
    slug: c.slug,
    name: c.name,
    polaris_name: c.polaris_name,
    storage_backend_id: c.storage_backend_id,
    storage_backend_kind: c.storage_backend_kind,
    storage_backend_name: c.storage_backend_name,
    storage_backend_root_uri: c.storage_backend_root_uri,
    created_at: c.created_at,
    is_default: c.is_default,
    attached_workspaces: c.workspace_ids.length,
  };
}

export const catalogHandlers = [
  http.get("/api/catalogs", () => HttpResponse.json(CATALOGS.map(out))),

  http.get("/api/workspaces/:ws/catalogs", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    return HttpResponse.json(catalogsForWorkspace(ws.id).map(out));
  }),

  http.post("/api/workspaces/:ws/catalogs", async ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const body = (await request.json()) as {
      name: string;
      storage_backend_id?: string;
      access_mode?: "open" | "scoped";
    };
    // A catalog's name is also its slug — identifier-safe.
    if (!/^[a-z][a-z0-9_]*$/.test(body.name)) {
      return httpError(422, "Invalid catalog name");
    }
    if (CATALOGS.some((c) => c.slug === body.name)) {
      return httpError(409, "Catalog name already taken");
    }
    const first = catalogsForWorkspace(ws.id).length === 0;
    const created = {
      id: `cat-${body.name}`,
      slug: body.name,
      name: body.name,
      polaris_name: body.name,
      // Chosen backend, else a bundled object store (matches the API default).
      storage_backend_id: body.storage_backend_id ?? "sb-bundled",
      storage_backend_kind: "object_store" as const,
      storage_backend_name: "Bundled object storage",
      storage_backend_root_uri: "",
      created_at: new Date().toISOString(),
      is_default: first,
      attached_workspaces: 1,
      workspace_ids: [ws.id],
      access_mode: body.access_mode ?? "open",
    };
    CATALOGS.push(created);
    return HttpResponse.json(out(created), { status: 201 });
  }),

  // A PUT on the attachment's own address, idempotent: 201 the first time,
  // 200 when it was already attached.
  http.put(
    "/api/workspaces/:ws/catalogs/:catalog",
    async ({ params, request }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      await request.json().catch(() => ({}));
      const cat = CATALOGS.find((c) => c.slug === params.catalog);
      if (!cat) return httpError(404, "Catalog not found");
      const already = cat.workspace_ids.includes(ws.id);
      if (!already) cat.workspace_ids.push(ws.id);
      return HttpResponse.json(out(cat), { status: already ? 200 : 201 });
    },
  ),

  http.delete("/api/workspaces/:ws/catalogs/:catalog", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const cat = CATALOGS.find(
      (c) => c.slug === params.catalog && c.workspace_ids.includes(ws.id),
    );
    if (!cat) return httpError(404, "Catalog not attached");
    cat.workspace_ids = cat.workspace_ids.filter((id) => id !== ws.id);
    return new HttpResponse(null, { status: 204 });
  }),

  http.delete("/api/catalogs/:id", ({ params }) => {
    const cat = findCatalogById(params.id as string);
    if (!cat) return httpError(404, "Catalog not found");
    if (cat.workspace_ids.length > 0) {
      return httpError(409, "Catalog is still attached to a workspace");
    }
    CATALOGS.splice(CATALOGS.indexOf(cat), 1);
    return new HttpResponse(null, { status: 204 });
  }),
];
