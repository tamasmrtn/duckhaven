import { http, HttpResponse } from "msw";

import { page } from "../lib/page";
import {
  QUERY_HISTORY,
  SAMPLE_PROFILE,
  SAVED_QUERIES,
} from "../fixtures/queries";
import { findWorkspace } from "../fixtures/workspaces";
import { SQL_METADATA } from "../fixtures/sqlMetadata";
import { CURRENT_USER, ALL_USERS } from "../fixtures/users";
import { nextId } from "../lib/seed";
import { httpError, validationError } from "../lib/errors";

/** Duration the History filters use: reported time, else wall clock.
 *
 * Mirrors `duration_expr()` server-side — a run that failed before the agent
 * reported a duration still has one, and a run that has not finished has none.
 */
function durationOf(r: {
  duration_ms: number | null;
  started_at: string;
  finished_at: string | null;
}): number | null {
  if (r.duration_ms != null) return r.duration_ms;
  if (!r.finished_at) return null;
  return Date.parse(r.finished_at) - Date.parse(r.started_at);
}
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

// Deterministic result rows for a finished query (no randomness). Spans
// multiple pages so cursor-based paging can be exercised.
const RESULT_ROW_COUNT = 120;

function generateResultRows(sql: string) {
  if (!sql.toLowerCase().includes("select")) return { rows: [], columns: [] };
  const rows = Array.from({ length: RESULT_ROW_COUNT }, (_, i) => ({
    d: new Date(Date.UTC(2026, 0, 1) - i * 86400000).toISOString().slice(0, 10),
    n: ((i * 317) % 15000) + 1000,
  }));
  return { rows, columns: ["d", "n"] };
}

export const queryHandlers = [
  http.post("/api/workspaces/:ws/queries", async ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");

    const body = (await request.json()) as {
      sql: string;
      agent_id: string;
      saved_query_id?: string;
    };
    if (!sqlAllowed(body.sql)) {
      return validationError(
        "sql_not_allowed",
        "Only read-only SELECT/WITH statements are allowed.",
      );
    }

    // Mirror the backend: a run from a saved query stamps its last_run_at.
    if (body.saved_query_id) {
      const saved = SAVED_QUERIES.find((q) => q.id === body.saved_query_id);
      if (saved) saved.last_run_at = new Date().toISOString();
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
      result_bytes: null,
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
        liveQueries[id].result_bytes = 4096;
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

  http.get("/api/queries/:id/profile", ({ params }) => {
    const id = params.id as string;
    const live = liveQueries[id];
    const hist = QUERY_HISTORY.find((q) => q.id === id);
    if (!live && !hist) return httpError(404, "Query not found");
    const status: QueryStatus = live ? live.status : (hist?.status ?? "done");
    const sql = live ? live.sql : (hist?.sql ?? "");
    // Only SELECTs carry a profile; DDL/failed queries return null.
    if (status === "done" && sql.toLowerCase().includes("select")) {
      return HttpResponse.json(SAMPLE_PROFILE);
    }
    return HttpResponse.json(null);
  }),

  http.get("/api/queries/:id/rows", ({ params, request }) => {
    const url = new URL(request.url);
    const limit = parseInt(url.searchParams.get("limit") ?? "50");
    const cursor = url.searchParams.get("cursor");
    const offset = cursor ? parseInt(cursor) : 0;
    const id = params.id as string;
    const live = liveQueries[id];
    const hist = QUERY_HISTORY.find((q) => q.id === id);

    if (!live && !hist) return httpError(404, "Query not found");

    const status: QueryStatus = live ? live.status : (hist?.status ?? "done");
    if (status !== "done") return httpError(409, "Query not done");

    const sql = live ? live.sql : (hist?.sql ?? "");
    const { rows, columns } = generateResultRows(sql);
    const page = rows.slice(offset, offset + limit);
    const nextOffset = offset + limit;
    return HttpResponse.json({
      rows: page,
      columns,
      cursor: nextOffset < rows.length ? String(nextOffset) : null,
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

  // Query log. Mirrors the server closely enough that the History page's
  // filters, sorting and cursor paging are exercised for real in tests rather
  // than being asserted against a handler that ignores them.
  http.get("/api/workspaces/:ws/queries", ({ params, request }) => {
    const url = new URL(request.url);
    const p = url.searchParams;
    const allWorkspaces = p.get("all_workspaces") === "true";
    const userId = p.get("user_id");
    const agentId = p.get("agent_id");
    const origin = p.get("origin");
    const sessionId = p.get("session_id");
    const since = p.get("since");
    const until = p.get("until");
    const q = p.get("q");
    const queryId = p.get("query_id");
    const statuses = p.getAll("status");
    const statementTypes = p.getAll("statement_type");
    const slowerThan = p.get("slower_than_ms");
    const sort = p.get("sort") ?? "started_at";
    const dir = p.get("dir") ?? "desc";
    const limit = Number(p.get("limit") ?? 100);
    const cursor = p.get("cursor");

    let pool = QUERY_HISTORY;
    if (!allWorkspaces) {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      pool = pool.filter((qr) => qr.workspace_id === ws.id);
    }

    // Machinery rather than someone's work; matches HIDDEN_ORIGINS server-side.
    const hidden = ["sample", "metadata", "maintenance"];
    const rows = pool
      .filter((r) => !r.origin || !hidden.includes(r.origin))
      .filter((r) => !userId || r.user_id === userId)
      .filter((r) => !agentId || r.agent_id === agentId)
      // Interactive runs carry a null origin; mirror the server's alias for it.
      .filter((r) =>
        !origin
          ? true
          : origin === "interactive"
            ? !r.origin
            : r.origin === origin,
      )
      .filter((r) => !sessionId || r.session_id === sessionId)
      .filter((r) => !since || r.started_at >= since)
      .filter((r) => !until || r.started_at <= until)
      .filter((r) => !q || r.sql.toLowerCase().includes(q.toLowerCase()))
      .filter((r) => !queryId || r.id.startsWith(queryId.toLowerCase()))
      .filter((r) => statuses.length === 0 || statuses.includes(r.status))
      .filter(
        (r) =>
          statementTypes.length === 0 ||
          (r.statement_type != null &&
            statementTypes.includes(r.statement_type)),
      )
      .filter((r) => {
        if (slowerThan == null) return true;
        const d = durationOf(r);
        return d != null && d >= Number(slowerThan);
      })
      .slice()
      .sort((a, b) => {
        const mul = dir === "asc" ? 1 : -1;
        if (sort === "duration") {
          const da = durationOf(a);
          const db = durationOf(b);
          // Unknown duration sorts last whichever way the list runs.
          if (da == null && db == null) return a.id < b.id ? -mul : mul;
          if (da == null) return 1;
          if (db == null) return -1;
          if (da !== db) return (da - db) * mul;
          return a.id < b.id ? -mul : mul;
        }
        if (a.started_at !== b.started_at)
          return a.started_at.localeCompare(b.started_at) * mul;
        return a.id < b.id ? -mul : mul;
      });

    // Opaque cursor over the sorted array, matching the server's contract
    // (a cursor names the last row of the previous page).
    const start = cursor
      ? rows.findIndex((r) => r.id === atob(cursor).split("|").pop()) + 1
      : 0;
    const page = rows.slice(start, start + limit);
    const hasMore = start + limit < rows.length;
    return HttpResponse.json({
      items: page,
      cursor: hasMore ? btoa(`x|${page[page.length - 1]?.id}`) : null,
      has_more: hasMore,
    });
  }),

  // Saved queries
  http.get("/api/workspaces/:ws/sql-metadata", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    return HttpResponse.json(SQL_METADATA);
  }),

  http.get("/api/workspaces/:ws/saved-queries", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    return page(
      SAVED_QUERIES.filter((q) => q.workspace_id === ws.id).map((q) => ({
        ...q,
        created_by_name:
          ALL_USERS.find((u) => u.id === q.created_by)?.name ?? null,
      })),
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
      // Overwrite by name: saving over an existing name updates that query.
      const existing = SAVED_QUERIES.find(
        (q) => q.workspace_id === ws.id && q.name === body.name,
      );
      if (existing) {
        existing.sql = body.sql;
        existing.default_agent_id = body.default_agent_id ?? null;
        return HttpResponse.json(existing, { status: 200 });
      }
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

  http.patch(
    "/api/workspaces/:ws/saved-queries/:id",
    async ({ params, request }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const saved = SAVED_QUERIES.find(
        (q) => q.id === params.id && q.workspace_id === ws.id,
      );
      if (!saved) return httpError(404, "Saved query not found");
      const body = (await request.json()) as {
        name?: string;
        sql?: string;
        default_agent_id?: string;
      };
      if (body.name !== undefined) saved.name = body.name;
      if (body.sql !== undefined) saved.sql = body.sql;
      if (body.default_agent_id !== undefined)
        saved.default_agent_id = body.default_agent_id;
      return HttpResponse.json(saved);
    },
  ),

  http.delete("/api/workspaces/:ws/saved-queries/:id", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const idx = SAVED_QUERIES.findIndex(
      (q) => q.id === params.id && q.workspace_id === ws.id,
    );
    if (idx === -1) return httpError(404, "Saved query not found");
    SAVED_QUERIES.splice(idx, 1);
    return new HttpResponse(null, { status: 204 });
  }),
];
