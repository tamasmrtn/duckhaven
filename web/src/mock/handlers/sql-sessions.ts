import { http, HttpResponse } from "msw";
import { SQL_SESSIONS, SESSION_STATEMENTS } from "../fixtures/sql-sessions";
import { findWorkspace } from "../fixtures/workspaces";
import { httpError } from "../lib/errors";

// Mirrors the server's admin-only affordances on the session list; `status` and
// `limit` stay open to any member, as they reveal nothing extra.
const ADMIN_ONLY_PARAMS = ["user_id", "agent_id", "since"];

export const sqlSessionHandlers = [
  http.get("/api/workspaces/:ws/sql/sessions", ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const url = new URL(request.url);
    if (ADMIN_ONLY_PARAMS.some((p) => url.searchParams.has(p))) {
      return httpError(403, "Forbidden");
    }
    const statuses = url.searchParams.getAll("status");
    return HttpResponse.json(
      SQL_SESSIONS.filter(
        (s) =>
          s.workspace_id === ws.id &&
          (statuses.length === 0 || statuses.includes(s.status)),
      ),
    );
  }),

  http.get("/api/sql/sessions/:id", ({ params }) => {
    const session = SQL_SESSIONS.find((s) => s.id === params.id);
    if (!session) return httpError(404, "Session not found");
    return HttpResponse.json(session);
  }),

  http.get("/api/sql/sessions/:id/statements", ({ params }) => {
    const session = SQL_SESSIONS.find((s) => s.id === params.id);
    if (!session) return httpError(404, "Session not found");
    // Execution order, ascending — the server orders by started_at.
    return HttpResponse.json(
      SESSION_STATEMENTS.filter((q) => q.session_id === params.id).sort(
        (a, b) => a.started_at.localeCompare(b.started_at),
      ),
    );
  }),

  http.delete("/api/sql/sessions/:id", ({ params }) => {
    const session = SQL_SESSIONS.find((s) => s.id === params.id);
    if (!session) return httpError(404, "Session not found");
    // The server flips the row to `closing` and waits for the agent's ack; the
    // mock lands on the terminal state directly.
    session.status = "closed";
    session.close_reason = "client";
    session.closed_at = new Date().toISOString();
    return new HttpResponse(null, { status: 204 });
  }),
];
