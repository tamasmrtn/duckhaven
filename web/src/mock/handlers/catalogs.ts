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
    const body = (await request.json()) as { slug: string; name: string };
    if (!/^[a-z][a-z0-9_]*$/.test(body.slug)) {
      return httpError(422, "Invalid catalog slug");
    }
    if (CATALOGS.some((c) => c.slug === body.slug)) {
      return httpError(409, "Catalog slug already taken");
    }
    const first = catalogsForWorkspace(ws.id).length === 0;
    const created = {
      id: `cat-${body.slug}`,
      slug: body.slug,
      name: body.name,
      polaris_name: body.slug,
      storage_backend_id: ws.storage_backend_id,
      storage_backend_kind: ws.storage_backend_kind,
      created_at: new Date().toISOString(),
      is_default: first,
      attached_workspaces: 1,
      workspace_ids: [ws.id],
    };
    CATALOGS.push(created);
    return HttpResponse.json(out(created), { status: 201 });
  }),

  http.post(
    "/api/workspaces/:ws/catalogs/attach",
    async ({ params, request }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const body = (await request.json()) as {
        catalog_id: string;
        make_default?: boolean;
      };
      const cat = findCatalogById(body.catalog_id);
      if (!cat) return httpError(404, "Catalog not found");
      if (cat.workspace_ids.includes(ws.id)) {
        return httpError(409, "Already attached");
      }
      cat.workspace_ids.push(ws.id);
      return HttpResponse.json(out(cat));
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
