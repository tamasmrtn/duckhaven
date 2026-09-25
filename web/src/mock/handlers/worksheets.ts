import { http, HttpResponse } from "msw";

import { WORKSHEETS } from "../fixtures/worksheets";
import { SAVED_QUERIES } from "../fixtures/queries";
import { findWorkspace } from "../fixtures/workspaces";
import { CURRENT_USER } from "../fixtures/users";
import { nextId } from "../lib/seed";
import { httpError, validationError } from "../lib/errors";
import type { Worksheet } from "@/types/worksheet";

// Mirrors api/routers/worksheets.py: private to the caller, content edits
// versioned (a stale base_version is a 409 carrying the current worksheet),
// everything else last-write-wins.

const CONTENT = ["sql", "title"] as const;
const META = [
  "agent_id",
  "catalog",
  "timeout_s",
  "saved_query_id",
  "last_query_id",
  "is_open",
  "tab_position",
] as const;

function own(wsId: string) {
  return WORKSHEETS.filter(
    (w) => w.workspace_id === wsId && w.owner_id === CURRENT_USER.id,
  );
}

function nextPosition(wsId: string): number {
  const open = own(wsId).filter((w) => w.is_open);
  return open.length ? Math.max(...open.map((w) => w.tab_position)) + 1 : 0;
}

function find(wsId: string, id: string): Worksheet | undefined {
  return own(wsId).find((w) => w.id === id);
}

function savedQueryMissing(wsId: string, id: unknown): boolean {
  return (
    typeof id === "string" &&
    !SAVED_QUERIES.some((q) => q.id === id && q.workspace_id === wsId)
  );
}

export const worksheetHandlers = [
  http.get("/api/workspaces/:ws/worksheets", ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const p = new URL(request.url).searchParams;
    const statuses = p.getAll("status");
    const savedQueryId = p.get("saved_query_id");
    const q = p.get("q")?.toLowerCase();
    const sort = p.get("sort") ?? "updated_at";
    const dir = p.get("dir") ?? (sort === "updated_at" ? "desc" : "asc");
    const limit = Number(p.get("limit") ?? 100);
    const cursor = p.get("cursor");

    const rows = own(ws.id)
      .filter((w) =>
        statuses.length === 1
          ? statuses[0] === "open"
            ? w.is_open
            : !w.is_open
          : true,
      )
      .filter((w) => !savedQueryId || w.saved_query_id === savedQueryId)
      .filter((w) => !q || w.title.toLowerCase().includes(q))
      .slice()
      .sort((a, b) => {
        const mul = dir === "desc" ? -1 : 1;
        const key =
          sort === "title"
            ? a.title.localeCompare(b.title)
            : sort === "position"
              ? a.tab_position - b.tab_position
              : a.updated_at.localeCompare(b.updated_at);
        return (key || a.id.localeCompare(b.id)) * mul;
      });

    const start = cursor ? rows.findIndex((r) => r.id === atob(cursor)) + 1 : 0;
    const items = rows.slice(start, start + limit);
    const hasMore = start + limit < rows.length;
    return HttpResponse.json({
      items,
      cursor: hasMore ? btoa(items[items.length - 1].id) : null,
      has_more: hasMore,
    });
  }),

  http.post("/api/workspaces/:ws/worksheets", async ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const body = (await request.json()) as Partial<Worksheet>;
    if (savedQueryMissing(ws.id, body.saved_query_id)) {
      return validationError(
        "saved_query_not_found",
        "No saved query with that id in this workspace",
      );
    }
    const now = new Date().toISOString();
    const isOpen = body.is_open ?? true;
    const sheet: Worksheet = {
      id: nextId("wk"),
      workspace_id: ws.id,
      owner_id: CURRENT_USER.id,
      title: body.title ?? "Untitled",
      sql: body.sql ?? "",
      agent_id: body.agent_id ?? null,
      catalog: body.catalog ?? null,
      timeout_s: body.timeout_s ?? null,
      saved_query_id: body.saved_query_id ?? null,
      last_query_id: null,
      is_open: isOpen,
      tab_position: isOpen ? nextPosition(ws.id) : 0,
      version: 1,
      created_at: now,
      updated_at: now,
    };
    WORKSHEETS.push(sheet);
    return HttpResponse.json(sheet, { status: 201 });
  }),

  http.get("/api/workspaces/:ws/worksheets/:id", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const sheet = find(ws.id, params.id as string);
    if (!sheet) return httpError(404, "Worksheet not found");
    return HttpResponse.json(sheet);
  }),

  http.patch(
    "/api/workspaces/:ws/worksheets/:id",
    async ({ params, request }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const sheet = find(ws.id, params.id as string);
      if (!sheet) return httpError(404, "Worksheet not found");
      const body = (await request.json()) as Partial<Worksheet> & {
        base_version?: number;
      };

      const content = CONTENT.filter((k) => body[k] != null);
      if (content.length > 0) {
        if (body.base_version == null) {
          return validationError(
            "base_version_required",
            "Editing sql or title needs the base_version it was made against",
          );
        }
        if (body.base_version !== sheet.version) {
          return validationError(
            "worksheet_conflict",
            "The worksheet changed elsewhere since that version",
            409,
            { current: { ...sheet } },
          );
        }
      }
      if (savedQueryMissing(ws.id, body.saved_query_id)) {
        return validationError(
          "saved_query_not_found",
          "No saved query with that id in this workspace",
        );
      }
      if (
        body.is_open === true &&
        !sheet.is_open &&
        body.tab_position == null
      ) {
        body.tab_position = nextPosition(ws.id);
      }
      const target = sheet as unknown as Record<string, unknown>;
      for (const k of content) target[k] = body[k];
      for (const k of META) {
        if (k in body) target[k] = body[k];
      }
      if (content.length > 0) {
        sheet.version += 1;
        sheet.updated_at = new Date().toISOString();
      }
      return HttpResponse.json({ ...sheet });
    },
  ),

  http.delete("/api/workspaces/:ws/worksheets/:id", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const idx = WORKSHEETS.findIndex(
      (w) =>
        w.id === params.id &&
        w.workspace_id === ws.id &&
        w.owner_id === CURRENT_USER.id,
    );
    if (idx === -1) return httpError(404, "Worksheet not found");
    WORKSHEETS.splice(idx, 1);
    return new HttpResponse(null, { status: 204 });
  }),
];
