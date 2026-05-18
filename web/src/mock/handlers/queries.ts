import { http, HttpResponse } from "msw";
import { QUERY_HISTORY, SAVED_QUERIES } from "../fixtures/queries";
import { findWorkspace } from "../fixtures/workspaces";
import type { Query, QueryStatus } from "@/types/query";

// In-memory store for in-flight queries
const liveQueries: Record<string, Query> = {};

function generateResultRows(sql: string) {
  const colMatch = sql.toLowerCase().includes("select");
  if (!colMatch) return { rows: [], columns: [] };

  const rows = Array.from({ length: 30 }, (_, i) => ({
    d: new Date(Date.now() - i * 86400000).toISOString().slice(0, 10),
    n: Math.floor(Math.random() * 15000) + 1000,
  }));
  return { rows, columns: ["d", "n"] };
}

export const queryHandlers = [
  http.post("/api/workspaces/:ws/queries", async ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return new HttpResponse(null, { status: 404 });

    const body = (await request.json()) as { sql: string; agent_id: string };
    const id = `q-live-${Date.now()}`;
    const query: Query = {
      id,
      workspace_id: ws.id,
      agent_id: body.agent_id,
      sql: body.sql,
      status: "queued",
      row_count: null,
      duration_ms: null,
      error: null,
      started_at: new Date().toISOString(),
      finished_at: null,
    };
    liveQueries[id] = query;

    // Simulate queued → running → done
    setTimeout(() => {
      if (liveQueries[id]) liveQueries[id].status = "running";
    }, 200);
    setTimeout(() => {
      if (liveQueries[id]) {
        liveQueries[id].status = "done";
        liveQueries[id].row_count = 30;
        liveQueries[id].duration_ms = 1400;
        liveQueries[id].finished_at = new Date().toISOString();
      }
    }, 1800);

    return HttpResponse.json({ id, status: "queued" }, { status: 202 });
  }),

  http.get("/api/queries/:id", ({ params }) => {
    const live = liveQueries[params.id as string];
    if (live) return HttpResponse.json(live);
    const hist = QUERY_HISTORY.find((q) => q.id === params.id);
    if (hist) return HttpResponse.json(hist);
    return new HttpResponse(null, { status: 404 });
  }),

  http.get("/api/queries/:id/rows", ({ params, request }) => {
    const url = new URL(request.url);
    const limit = parseInt(url.searchParams.get("limit") ?? "50");
    const id = params.id as string;
    const live = liveQueries[id];
    const hist = QUERY_HISTORY.find((q) => q.id === id);

    if (!live && !hist) return new HttpResponse(null, { status: 404 });

    const status: QueryStatus = live ? live.status : (hist?.status ?? "done");
    if (status !== "done") {
      return HttpResponse.json({
        rows: [],
        columns: [],
        cursor: null,
        total: 0,
      });
    }

    const sql = live ? live.sql : (hist?.sql ?? "");
    const { rows, columns } = generateResultRows(sql);
    const page = rows.slice(0, limit);
    return HttpResponse.json({
      rows: page,
      columns,
      cursor: null,
      total: rows.length,
    });
  }),

  http.delete("/api/queries/:id", ({ params }) => {
    const id = params.id as string;
    if (liveQueries[id]) {
      liveQueries[id].status = "cancelled";
      liveQueries[id].finished_at = new Date().toISOString();
    }
    return new HttpResponse(null, { status: 204 });
  }),

  // Saved queries
  http.get("/api/workspaces/:ws/saved-queries", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return new HttpResponse(null, { status: 404 });
    return HttpResponse.json(
      SAVED_QUERIES.filter((q) => q.workspace_id === ws.id),
    );
  }),

  http.post(
    "/api/workspaces/:ws/saved-queries",
    async ({ params, request }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return new HttpResponse(null, { status: 404 });
      const body = (await request.json()) as {
        name: string;
        sql: string;
        default_agent_id?: string;
      };
      const saved = {
        id: `sq-${Date.now()}`,
        name: body.name,
        sql: body.sql,
        workspace_id: ws.id,
        default_agent_id: body.default_agent_id ?? null,
        created_by: "marton@duckhaven.local",
        created_at: new Date().toISOString(),
        last_run_at: null,
      };
      SAVED_QUERIES.push(saved);
      return HttpResponse.json(saved, { status: 201 });
    },
  ),

  // Audit
  http.get("/api/admin/audit", () => {
    return HttpResponse.json(QUERY_HISTORY);
  }),
];
