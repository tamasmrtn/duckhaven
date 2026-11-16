import { http, HttpResponse } from "msw";
import { SCHEMAS } from "../fixtures/schemas";
import {
  CATALOG_SCHEMAS,
  catalogsForWorkspace,
  defaultCatalogSlug,
} from "../fixtures/catalogs";
import { SAVED_QUERIES } from "../fixtures/queries";
import { DASHBOARDS } from "../fixtures/dashboards";
import { SEMANTIC_MODELS } from "../fixtures/semantic";
import { findWorkspace } from "../fixtures/workspaces";
import { httpError } from "../lib/errors";
import type { SearchResult } from "@/types/search";

export const searchHandlers = [
  http.get("/api/workspaces/:ws/search", ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");

    const url = new URL(request.url);
    const needle = (url.searchParams.get("q") ?? "").trim().toLowerCase();
    if (!needle) return HttpResponse.json({ items: [], has_more: false });
    const requested = url.searchParams.getAll("types");
    const wanted = new Set(
      requested.length
        ? requested
        : [
            "catalog",
            "schema",
            "table",
            "saved_query",
            "dashboard",
            "semantic_model",
          ],
    );
    const limit = Number(url.searchParams.get("limit") ?? 20);

    // The workspace's own objects first, as the server orders them, so many
    // table matches cannot push them past the limit.
    const results: SearchResult[] = [];
    for (const sq of SAVED_QUERIES) {
      if (!wanted.has("saved_query")) break;
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
    // Scoped as their own list handlers scope them: dashboards to ws-1, models
    // to every workspace.
    const dashboards =
      wanted.has("dashboard") && ws.id === "ws-1" ? DASHBOARDS : [];
    for (const d of dashboards) {
      if (`${d.name} ${d.title}`.toLowerCase().includes(needle)) {
        results.push({
          type: "dashboard",
          name: d.name,
          title: d.title,
          id: d.id,
        });
      }
    }
    for (const m of wanted.has("semantic_model") ? SEMANTIC_MODELS : []) {
      if (`${m.slug} ${m.name}`.toLowerCase().includes(needle)) {
        results.push({
          type: "semantic_model",
          name: m.slug,
          title: m.name,
          id: m.id,
        });
      }
    }

    const def = defaultCatalogSlug(ws.id);

    const catalogStores: { slug: string; list: (typeof SCHEMAS)[string] }[] = [
      ...(def ? [{ slug: def, list: SCHEMAS[ws.id] ?? [] }] : []),
      ...catalogsForWorkspace(ws.id)
        .filter((c) => c.slug !== def)
        .map((c) => ({ slug: c.slug, list: CATALOG_SCHEMAS[c.slug] ?? [] })),
    ];

    for (const { slug, list } of catalogStores) {
      if (wanted.has("catalog") && slug.toLowerCase().includes(needle)) {
        results.push({ type: "catalog", catalog: slug, name: slug });
      }
      for (const schema of list) {
        if (
          wanted.has("schema") &&
          schema.name.toLowerCase().includes(needle)
        ) {
          results.push({ type: "schema", catalog: slug, name: schema.name });
        }
        for (const table of schema.tables) {
          if (
            wanted.has("table") &&
            table.name.toLowerCase().includes(needle)
          ) {
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

    // A search report: truncated by limit, no cursor to walk.
    return HttpResponse.json({
      items: results.slice(0, limit),
      has_more: results.length > limit,
    });
  }),
];
