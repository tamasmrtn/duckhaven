import { http, HttpResponse } from "msw";
import { SCHEMAS } from "../fixtures/schemas";
import {
  CATALOG_SCHEMAS,
  catalogsForWorkspace,
  defaultCatalogSlug,
} from "../fixtures/catalogs";
import { SAVED_QUERIES } from "../fixtures/queries";
import { findWorkspace } from "../fixtures/workspaces";
import { httpError } from "../lib/errors";
import type { SearchResult } from "@/types/search";

export const searchHandlers = [
  http.get("/api/workspaces/:ws/search", ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");

    const needle = (new URL(request.url).searchParams.get("q") ?? "")
      .trim()
      .toLowerCase();
    if (!needle) return HttpResponse.json([]);

    const results: SearchResult[] = [];
    const def = defaultCatalogSlug(ws.id);

    const catalogStores: { slug: string; list: (typeof SCHEMAS)[string] }[] = [
      ...(def ? [{ slug: def, list: SCHEMAS[ws.id] ?? [] }] : []),
      ...catalogsForWorkspace(ws.id)
        .filter((c) => c.slug !== def)
        .map((c) => ({ slug: c.slug, list: CATALOG_SCHEMAS[c.slug] ?? [] })),
    ];

    for (const { slug, list } of catalogStores) {
      for (const schema of list) {
        if (schema.name.toLowerCase().includes(needle)) {
          results.push({ type: "schema", catalog: slug, name: schema.name });
        }
        for (const table of schema.tables) {
          if (table.name.toLowerCase().includes(needle)) {
            results.push({
              type: "table",
              catalog: slug,
              schema_name: schema.name,
              name: table.name,
            });
          }
        }
      }
    }

    for (const sq of SAVED_QUERIES) {
      if (sq.workspace_id === ws.id && sq.name.toLowerCase().includes(needle)) {
        results.push({
          type: "saved_query",
          name: sq.name,
          id: sq.id,
          sql: sq.sql,
          default_agent_id: sq.default_agent_id,
        });
      }
    }

    return HttpResponse.json(results.slice(0, 20));
  }),
];
