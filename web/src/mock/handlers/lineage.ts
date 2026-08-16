import { http, HttpResponse } from "msw";
import { LINEAGE, makeEmptyLineage } from "../fixtures/lineage";
import type { LineageGraph } from "@/types/lineage";

// Only `analytics.daily_active_users` has lineage in the fixtures; every other
// table returns the empty graph, so the empty state is reachable in dev without
// a handler override.
const SEEDED_TABLE = "daily_active_users";

function filtered(
  direction: string | null,
  depth: number,
  columnsFor: string[],
): LineageGraph {
  const nodes = LINEAGE.nodes.filter((n) => {
    if (Math.abs(n.distance) > depth) return false;
    if (direction === "upstream") return n.distance <= 0;
    if (direction === "downstream") return n.distance >= 0;
    return true;
  });
  const keys = new Set(nodes.map((n) => n.key));
  // Mirrors the API: column detail comes back only for edges touching a node the
  // caller named, so a client that asks for none gets none.
  const wanted = new Set(columnsFor);
  return {
    ...LINEAGE,
    nodes,
    edges: LINEAGE.edges
      .filter((e) => keys.has(e.source_key) && keys.has(e.target_key))
      .map((e) =>
        wanted.has(e.source_key) || wanted.has(e.target_key)
          ? e
          : { ...e, columns: [] },
      ),
  };
}

export const lineageHandlers = [
  http.get(
    "/api/workspaces/:ws/catalogs/:catalog/schemas/:schema/tables/:table/lineage",
    ({ params, request }) => {
      const url = new URL(request.url);
      if (params.table !== SEEDED_TABLE) {
        return HttpResponse.json(
          makeEmptyLineage(`cat:0/${params.schema}/${params.table}`),
        );
      }
      return HttpResponse.json(
        filtered(
          url.searchParams.get("direction"),
          Number(url.searchParams.get("depth") ?? 2),
          url.searchParams.getAll("columns_for"),
        ),
      );
    },
  ),
  http.post("/api/workspaces/:ws/lineage/imports", async () => {
    return HttpResponse.json({
      created: 2,
      updated: 0,
      removed: 0,
      skipped: [],
    });
  }),
  http.post("/api/workspaces/:ws/lineage/imports/:provider", async () => {
    return HttpResponse.json({
      created: 5,
      updated: 1,
      removed: 0,
      skipped: [],
    });
  }),
  http.delete("/api/workspaces/:ws/lineage/imports", () => {
    return HttpResponse.json({
      created: 0,
      updated: 0,
      removed: 3,
      skipped: [],
    });
  }),
];
