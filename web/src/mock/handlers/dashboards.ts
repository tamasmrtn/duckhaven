import { http, HttpResponse } from "msw";
import { parseDocument } from "yaml";
import {
  DASHBOARD_SCHEMA,
  DASHBOARDS,
  DRAFTS,
  PREVIEWS,
  REVISIONS,
  RUNS,
} from "../fixtures/dashboards";
import { findWorkspace } from "../fixtures/workspaces";
import { httpError, validationError } from "../lib/errors";
import { page } from "../lib/page";
import type {
  Dashboard,
  DashboardFile,
  DashboardPreview,
  DashboardRunRequest,
  DashboardSummary,
  Diagnostic,
  RunEvent,
  RunStart,
  RunWidget,
} from "@/types/dashboard";

// Mirrors api/routers/dashboards.py. Only acme-analytics (ws-1) has dashboards.
// A run streams `start`, a `compute` event when some of its widgets wait for
// compute, each widget's answer, then `done`. A draft is kept apart from the
// published file until it is published; a preview's widgets answer as the
// dashboard widget with the same id does.

const STEP_MS = 15;

function summary(d: Dashboard): DashboardSummary {
  const { id, name, title, description, labels, status, has_draft, source } = d;
  return {
    id,
    name,
    title,
    description,
    labels,
    status,
    has_draft,
    source,
    source_id: d.source_id,
    managed: d.managed,
    version: d.version,
    created_at: d.created_at,
    updated_at: d.updated_at,
    updated_by: d.updated_by,
    updated_via: d.updated_via,
    published_at: d.published_at,
  };
}

function find(ws: string, name: string): Dashboard | undefined {
  const workspace = findWorkspace(ws);
  if (workspace?.id !== "ws-1") return undefined;
  return DASHBOARDS.find((d) => d.name === name);
}

/** The dashboard with its draft shown, as `?draft=true` reads it. */
function withDraft(d: Dashboard): Dashboard {
  const draft = DRAFTS[d.name];
  return draft
    ? { ...d, draft: true, spec: draft.spec, source_text: draft.source_text }
    : d;
}

function missing(name: unknown) {
  return httpError(404, `No dashboard '${String(name)}'.`);
}

/** A file as the server's loader reads it, or what is wrong with it. */
function load(
  text: string,
): { spec: DashboardFile } | { diagnostics: Diagnostic[] } {
  const doc = parseDocument(text, { uniqueKeys: true });
  if (doc.errors.length) {
    return {
      diagnostics: doc.errors.map((e) => ({
        path: "",
        line: e.linePos?.[0].line ?? null,
        col: e.linePos?.[0].col ?? null,
        message: e.message.split("\n")[0],
        hint: null,
      })),
    };
  }
  const spec = doc.toJS() as Partial<DashboardFile> | null;
  const absent = ["version", "name", "title", "rows"].filter(
    (key) => spec?.[key as keyof DashboardFile] == null,
  );
  if (absent.length) {
    return {
      diagnostics: absent.map((key) => ({
        path: key,
        line: 1,
        col: 1,
        message: `The file needs \`${key}\`.`,
        hint: null,
      })),
    };
  }
  return { spec: spec as DashboardFile };
}

function invalid(diagnostics: Diagnostic[]) {
  return validationError(
    "invalid_dashboard",
    diagnostics.length === 1
      ? diagnostics[0].message
      : `${diagnostics.length} problems; the first: ${diagnostics[0].message}`,
    422,
    { diagnostics },
  );
}

function conflict(d: Dashboard, base: number) {
  return validationError(
    "dashboard_conflict",
    `'${d.name}' changed since version ${base}; it is at version ${d.version}.`,
    409,
    { current: withDraft(d) },
  );
}

function stamp(d: Dashboard) {
  d.version += 1;
  d.updated_at = new Date().toISOString();
  d.updated_by = "u-1";
  d.updated_via = "ui";
}

function stream(events: RunEvent[]) {
  const encoder = new TextEncoder();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const body = new ReadableStream({
    start(controller) {
      let i = 0;
      const next = () => {
        if (i === events.length) {
          controller.close();
          return;
        }
        controller.enqueue(
          encoder.encode(`data: ${JSON.stringify(events[i++])}\n\n`),
        );
        timer = setTimeout(next, STEP_MS);
      };
      timer = setTimeout(next, STEP_MS);
    },
    cancel() {
      clearTimeout(timer);
    },
  });
  return new HttpResponse(body, {
    headers: { "Content-Type": "text/event-stream" },
  });
}

/** A run of `spec`: the answers asked for, the cold ones after compute starts. */
function runEvents(
  spec: DashboardFile,
  about: RunStart["dashboard"],
  answers: RunWidget[],
  coldIds: string[],
  body: DashboardRunRequest,
): RunEvent[] {
  const asked = body.widgets
    ? answers.filter((w) => body.widgets!.includes(w.id))
    : answers;
  const cold = coldIds.filter((id) => asked.some((w) => w.id === id));
  const parameters = Object.entries(spec.parameters ?? {}).map(([name, p]) => ({
    name,
    type: p.type,
    value: body.params?.[name] ?? p.default ?? null,
    window:
      p.type === "time_range"
        ? {
            start: "2026-10-15",
            end: "2026-11-14",
            text: "Last 30 days: 15 Oct – 13 Nov 2026",
          }
        : null,
  }));
  const warm = asked.filter((w) => !cold.includes(w.id));
  const later = asked.filter((w) => cold.includes(w.id));
  const events: RunEvent[] = [
    {
      type: "start",
      run_id: `run-${about.preview ?? about.name}`,
      dashboard: about,
      today: "2026-11-13",
      timezone: "UTC",
      parameters,
      widgets: asked.map((w) => w.id),
    },
    ...(cold.length
      ? [
          {
            type: "compute" as const,
            state: "starting" as const,
            widgets: cold,
          },
        ]
      : []),
    ...[...warm, ...later].map((w, i) => ({
      ...w,
      timing: { elapsed_ms: 40 + i * 30, query_ms: 25 },
    })),
  ];
  const states: Record<string, number> = {};
  for (const w of asked) states[w.state] = (states[w.state] ?? 0) + 1;
  events.push({
    type: "done",
    elapsed_ms: 40 + asked.length * 30,
    widgets: states,
    statements: asked.length,
    cache_hits: asked.filter((w) => w.cache === "hit").length,
    batch: { used: true, batches: 1 },
  });
  return events;
}

/** What a preview's data widgets answer: the fixture with the same id, or nothing. */
function previewAnswers(spec: DashboardFile): RunWidget[] {
  const known = Object.values(RUNS).flatMap((r) => r.widgets);
  return spec.rows
    .flatMap((row) => row.widgets)
    .filter((w) => w.type !== "text")
    .map(
      (w) =>
        known.find((k) => k.id === w.id) ?? {
          type: "widget" as const,
          id: w.id,
          state: "empty" as const,
          scope: {
            window: null,
            compare_window: null,
            applied: [],
            skipped: [],
          },
        },
    );
}

/** A short hash of the text, so a preview's changes are told apart. */
function hash(text: string): string {
  let h = 0;
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) | 0;
  return `hash-${(h >>> 0).toString(16)}`;
}

let previewSeq = 0;

async function readBody<T>(request: Request): Promise<Partial<T>> {
  return ((await request.json().catch(() => ({}))) ?? {}) as Partial<T>;
}

export const dashboardHandlers = [
  http.get("/api/dashboard-schema", () => HttpResponse.json(DASHBOARD_SCHEMA)),

  http.get("/api/workspaces/:ws/dashboards", ({ params }) => {
    const workspace = findWorkspace(params.ws as string);
    if (!workspace) return httpError(404, "Workspace not found.");
    return page(workspace.id === "ws-1" ? DASHBOARDS.map(summary) : []);
  }),

  http.post("/api/workspaces/:ws/dashboards", async ({ params, request }) => {
    if (findWorkspace(params.ws as string)?.id !== "ws-1") {
      return httpError(404, "Workspace not found.");
    }
    const { source_text = "" } = await readBody<{ source_text: string }>(
      request,
    );
    const loaded = load(source_text);
    if ("diagnostics" in loaded) return invalid(loaded.diagnostics);
    const { spec } = loaded;
    if (DASHBOARDS.some((d) => d.name === spec.name)) {
      return validationError(
        "dashboard_name_taken",
        `This workspace already has a dashboard called '${spec.name}'.`,
        409,
      );
    }
    const now = new Date().toISOString();
    const created: Dashboard = {
      id: `dash-${spec.name}`,
      name: spec.name,
      title: spec.title,
      description: spec.description ?? null,
      labels: spec.labels ?? [],
      status: "draft",
      has_draft: true,
      source: "ui",
      source_id: null,
      managed: false,
      version: 1,
      created_at: now,
      updated_at: now,
      updated_by: "u-1",
      updated_via: "ui",
      published_at: null,
      draft: true,
      spec,
      source_text,
      spec_hash: hash(source_text),
      spec_version: 1,
      source_ref: null,
      client: null,
      created_by: "u-1",
      published_by: null,
      refs: [],
      warnings: [],
    };
    DASHBOARDS.push(created);
    return HttpResponse.json(created, { status: 201 });
  }),

  http.get("/api/workspaces/:ws/dashboards/:name", ({ params, request }) => {
    const d = find(params.ws as string, params.name as string);
    if (!d) return missing(params.name);
    const draft = new URL(request.url).searchParams.get("draft") === "true";
    return HttpResponse.json(draft ? withDraft(d) : d);
  }),

  http.patch(
    "/api/workspaces/:ws/dashboards/:name",
    async ({ params, request }) => {
      const d = find(params.ws as string, params.name as string);
      if (!d) return missing(params.name);
      if (d.managed) {
        return validationError(
          "managed_dashboard",
          `'${d.name}' is managed in git. Change its file there and apply it.`,
          409,
        );
      }
      const body = await readBody<{
        source_text: string;
        base_version: number;
      }>(request);
      if (body.base_version !== d.version) {
        return conflict(d, body.base_version ?? 0);
      }
      const text = body.source_text ?? "";
      const loaded = load(text);
      if ("diagnostics" in loaded) return invalid(loaded.diagnostics);
      stamp(d);
      if (d.status === "draft") {
        // Never published: the draft is all there is, and a new name applies at once.
        Object.assign(d, {
          spec: loaded.spec,
          source_text: text,
          name: loaded.spec.name,
          title: loaded.spec.title,
        });
        return HttpResponse.json(d);
      }
      DRAFTS[d.name] = { spec: loaded.spec, source_text: text };
      d.has_draft = true;
      return HttpResponse.json(withDraft(d));
    },
  ),

  http.delete("/api/workspaces/:ws/dashboards/:name/draft", ({ params }) => {
    const d = find(params.ws as string, params.name as string);
    if (!d) return missing(params.name);
    if (d.status === "draft") {
      return validationError(
        "dashboard_unpublished",
        `'${d.name}' has never been published, so its draft is all there is. Delete the dashboard instead.`,
        409,
      );
    }
    if (DRAFTS[d.name]) {
      delete DRAFTS[d.name];
      d.has_draft = false;
      stamp(d);
    }
    return new HttpResponse(null, { status: 204 });
  }),

  http.post(
    "/api/workspaces/:ws/dashboards/:name/publish",
    async ({ params, request }) => {
      const d = find(params.ws as string, params.name as string);
      if (!d) return missing(params.name);
      const body = await readBody<{
        base_version: number;
        message: string | null;
      }>(request);
      if (body.base_version !== d.version) {
        return conflict(d, body.base_version ?? 0);
      }
      const draft =
        DRAFTS[d.name] ??
        (d.status === "draft"
          ? { spec: d.spec, source_text: d.source_text }
          : null);
      if (!draft) return HttpResponse.json(d);
      const now = new Date().toISOString();
      const revisions = (REVISIONS[d.name] ??= []);
      revisions.unshift({
        number: (revisions[0]?.number ?? 0) + 1,
        spec_hash: hash(JSON.stringify(draft.spec)),
        message: body.message ?? null,
        published_by: "u-1",
        published_at: now,
        spec: draft.spec,
        source_text: draft.source_text,
      });
      delete DRAFTS[d.name];
      stamp(d);
      Object.assign(d, {
        spec: draft.spec,
        source_text: draft.source_text,
        title: draft.spec.title,
        status: "published",
        has_draft: false,
        draft: false,
        published_at: now,
        published_by: "u-1",
      });
      return HttpResponse.json(d);
    },
  ),

  http.get("/api/workspaces/:ws/dashboards/:name/revisions", ({ params }) => {
    const d = find(params.ws as string, params.name as string);
    if (!d) return missing(params.name);
    return page(
      (REVISIONS[d.name] ?? []).map(({ spec: _s, source_text: _t, ...r }) => r),
    );
  }),

  http.get(
    "/api/workspaces/:ws/dashboards/:name/revisions/:number",
    ({ params }) => {
      const d = find(params.ws as string, params.name as string);
      if (!d) return missing(params.name);
      const r = (REVISIONS[d.name] ?? []).find(
        (x) => x.number === Number(params.number),
      );
      if (!r) {
        return httpError(
          404,
          `'${d.name}' has no revision ${String(params.number)}.`,
        );
      }
      return HttpResponse.json(r);
    },
  ),

  http.post(
    "/api/workspaces/:ws/dashboard-validations",
    async ({ request }) => {
      const { source_text = "" } = await readBody<{ source_text: string }>(
        request,
      );
      const loaded = load(source_text);
      return HttpResponse.json(
        "diagnostics" in loaded
          ? { valid: false, errors: loaded.diagnostics, warnings: [], refs: [] }
          : { valid: true, errors: [], warnings: [], refs: [] },
      );
    },
  ),

  http.post("/api/workspaces/:ws/dashboard-previews", async ({ request }) => {
    const { source_text = "" } = await readBody<{ source_text: string }>(
      request,
    );
    const loaded = load(source_text);
    if ("diagnostics" in loaded) return invalid(loaded.diagnostics);
    const now = new Date().toISOString();
    const preview: DashboardPreview = {
      id: `preview-${++previewSeq}`,
      spec: loaded.spec,
      source_text,
      spec_hash: hash(source_text),
      created_at: now,
      updated_at: now,
      expires_at: new Date(Date.now() + 2 * 3600_000).toISOString(),
      warnings: [],
    };
    PREVIEWS[preview.id] = preview;
    return HttpResponse.json(preview, { status: 201 });
  }),

  http.put(
    "/api/workspaces/:ws/dashboard-previews/:id",
    async ({ params, request }) => {
      const preview = PREVIEWS[params.id as string];
      if (!preview) return httpError(404, `No preview ${String(params.id)}.`);
      const { source_text = "" } = await readBody<{ source_text: string }>(
        request,
      );
      const loaded = load(source_text);
      if ("diagnostics" in loaded) return invalid(loaded.diagnostics);
      Object.assign(preview, {
        spec: loaded.spec,
        source_text,
        spec_hash: hash(source_text),
        updated_at: new Date().toISOString(),
      });
      return HttpResponse.json(preview);
    },
  ),

  http.post(
    "/api/workspaces/:ws/dashboard-previews/:id/run",
    async ({ params, request }) => {
      const preview = PREVIEWS[params.id as string];
      if (!preview) return httpError(404, `No preview ${String(params.id)}.`);
      const body = await readBody<DashboardRunRequest>(request);
      return stream(
        runEvents(
          preview.spec,
          {
            name: preview.spec.name,
            version: null,
            spec_hash: preview.spec_hash,
            draft: false,
            preview: preview.id,
          },
          previewAnswers(preview.spec),
          [],
          body,
        ),
      );
    },
  ),

  http.post(
    "/api/workspaces/:ws/dashboards/:name/run",
    async ({ params, request }) => {
      const d = find(params.ws as string, params.name as string);
      if (!d) return missing(params.name);
      const body = await readBody<DashboardRunRequest>(request);
      const run = RUNS[d.name] ?? { widgets: [] };
      return stream(
        runEvents(
          d.spec,
          {
            name: d.name,
            version: d.version,
            spec_hash: d.spec_hash,
            draft: d.draft,
            preview: null,
          },
          run.widgets,
          run.cold ?? [],
          body,
        ),
      );
    },
  ),
];
