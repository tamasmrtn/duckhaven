import { http, HttpResponse } from "msw";
import { QUERY_HISTORY, SAVED_QUERIES } from "../fixtures/queries";
import { findWorkspace } from "../fixtures/workspaces";
import { CURRENT_USER } from "../fixtures/users";
import { nextId } from "../lib/seed";
import { httpError, validationError } from "../lib/errors";
import type { Query, QueryStatus } from "@/types/query";

// In-memory store for in-flight queries (rebuilt per test via resetLiveQueries).
let liveQueries: Record<string, Query> = {};

export function resetLiveQueries(): void {
  liveQueries = {};
}

// Mirrors the backend SQL guard: data statements + catalog DDL are allowed;
// sandbox-escaping statements (ATTACH, COPY, LOAD, SET, …) are not.
const ALLOWED_HEADS = [
  "select",
  "with",
  "insert",
  "update",
  "delete",
  "merge",
  "create",
  "alter",
  "drop",
];

function sqlAllowed(sql: string): boolean {
  const head = sql.trim().toLowerCase();
  return ALLOWED_HEADS.some((kw) => head.startsWith(kw));
}

// Deterministic result rows for a finished query (no randomness).
function generateResultRows(sql: string) {
  if (!sql.toLowerCase().includes("select")) return { rows: [], columns: [] };
  const rows = Array.from({ length: 30 }, (_, i) => ({
    d: new Date(Date.UTC(2026, 0, 1) - i * 86400000).toISOString().slice(0, 10),
    n: ((i * 317) % 15000) + 1000,
  }));
  return { rows, columns: ["d", "n"] };
}

export const queryHandlers = [
  http.post("/api/workspaces/:ws/queries", async ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");

    const body = (await request.json()) as { sql: string; agent_id: string };
    if (!sqlAllowed(body.sql)) {
      return validationError(
        "sql_not_allowed",
        "Only read-only SELECT/WITH statements are allowed.",
      );
    }

    const id = nextId("q");
    const query: Query = {
      id,
      workspace_id: ws.id,
      agent_id: body.agent_id,
      user_id: CURRENT_USER.id,
      sql: body.sql,
      status: "queued",
      row_count: null,
      duration_ms: null,
      error: null,
      progress: null,
      started_at: new Date().toISOString(),
      finished_at: null,
    };
    liveQueries[id] = query;

    // Simulate queued → running → done
    setTimeout(() => {
      if (liveQueries[id]) {
        liveQueries[id].status = "running";
        liveQueries[id].progress = { stage: "scanning" };
      }
    }, 200);
    setTimeout(() => {
      if (liveQueries[id]) {
        liveQueries[id].status = "done";
        liveQueries[id].row_count = 30;
        liveQueries[id].duration_ms = 1400;
        liveQueries[id].progress = null;
        liveQueries[id].finished_at = new Date().toISOString();
      }
    }, 1800);

    return HttpResponse.json(query, { status: 202 });
  }),

  http.get("/api/queries/:id", ({ params }) => {
    const live = liveQueries[params.id as string];
    if (live) return HttpResponse.json(live);
    const hist = QUERY_HISTORY.find((q) => q.id === params.id);
    if (hist) return HttpResponse.json(hist);
    return httpError(404, "Query not found");
  }),

  http.get("/api/queries/:id/rows", ({ params, request }) => {
    const url = new URL(request.url);
    const limit = parseInt(url.searchParams.get("limit") ?? "50");
    const id = params.id as string;
    const live = liveQueries[id];
    const hist = QUERY_HISTORY.find((q) => q.id === id);

    if (!live && !hist) return httpError(404, "Query not found");

    const status: QueryStatus = live ? live.status : (hist?.status ?? "done");
    if (status !== "done") return httpError(409, "Query not done");

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

  // Workspace-scoped query history (member-accessible), newest first.
  http.get("/api/workspaces/:ws/queries", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const rows = QUERY_HISTORY.filter((q) => q.workspace_id === ws.id)
      .slice()
      .sort((a, b) => b.started_at.localeCompare(a.started_at));
    return HttpResponse.json(rows);
  }),

  // Saved queries
  http.get("/api/workspaces/:ws/saved-queries", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    return HttpResponse.json(
      SAVED_QUERIES.filter((q) => q.workspace_id === ws.id),
    );
  }),

  http.post(
    "/api/workspaces/:ws/saved-queries",
    async ({ params, request }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const body = (await request.json()) as {
        name: string;
        sql: string;
        default_agent_id?: string;
      };
      const saved = {
        id: nextId("sq"),
        name: body.name,
        sql: body.sql,
        workspace_id: ws.id,
        default_agent_id: body.default_agent_id ?? null,
        created_by: CURRENT_USER.id,
        created_at: new Date().toISOString(),
        last_run_at: null,
      };
      SAVED_QUERIES.push(saved);
      return HttpResponse.json(saved, { status: 201 });
    },
  ),

  // Audit — supports the backend's filter params, ordered started_at DESC.
  http.get("/api/admin/audit", ({ request }) => {
    const url = new URL(request.url);
    const workspaceId = url.searchParams.get("workspace_id");
    const agentId = url.searchParams.get("agent_id");
    const userId = url.searchParams.get("user_id");
    const since = url.searchParams.get("since");
    const until = url.searchParams.get("until");
    const limit = parseInt(url.searchParams.get("limit") ?? "100");

    const rows = QUERY_HISTORY.filter((q) => {
      if (workspaceId && q.workspace_id !== workspaceId) return false;
      if (agentId && q.agent_id !== agentId) return false;
      if (userId && q.user_id !== userId) return false;
      if (since && q.started_at < since) return false;
      if (until && q.started_at > until) return false;
      return true;
    })
      .slice()
      .sort((a, b) => b.started_at.localeCompare(a.started_at))
      .slice(0, limit);

    return HttpResponse.json(rows);
  }),
];
