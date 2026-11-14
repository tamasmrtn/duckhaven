import { http, HttpResponse } from "msw";
import { DASHBOARDS, RUNS } from "../fixtures/dashboards";
import { findWorkspace } from "../fixtures/workspaces";
import { httpError } from "../lib/errors";
import { page } from "../lib/page";
import type {
  Dashboard,
  DashboardRunRequest,
  DashboardSummary,
  RunEvent,
} from "@/types/dashboard";

// Mirrors api/routers/dashboards.py for reading and running. Only acme-analytics
// (ws-1) has dashboards. A run streams `start`, a `compute` event when some of
// its widgets wait for compute, each widget's answer, then `done`.

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

export const dashboardHandlers = [
  http.get("/api/workspaces/:ws/dashboards", ({ params }) => {
    const workspace = findWorkspace(params.ws as string);
    if (!workspace) return httpError(404, "Workspace not found.");
    return page(workspace.id === "ws-1" ? DASHBOARDS.map(summary) : []);
  }),

  http.get("/api/workspaces/:ws/dashboards/:name", ({ params }) => {
    const d = find(params.ws as string, params.name as string);
    if (!d) return httpError(404, `No dashboard '${params.name as string}'.`);
    return HttpResponse.json(d);
  }),

  http.post(
    "/api/workspaces/:ws/dashboards/:name/run",
    async ({ params, request }) => {
      const d = find(params.ws as string, params.name as string);
      if (!d) return httpError(404, `No dashboard '${params.name as string}'.`);
      const body = ((await request.json().catch(() => ({}))) ??
        {}) as DashboardRunRequest;
      const run = RUNS[d.name] ?? { widgets: [] };
      const asked = body.widgets
        ? run.widgets.filter((w) => body.widgets!.includes(w.id))
        : run.widgets;
      const cold = (run.cold ?? []).filter((id) =>
        asked.some((w) => w.id === id),
      );
      const parameters = Object.entries(d.spec.parameters ?? {}).map(
        ([name, p]) => ({
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
        }),
      );
      const warm = asked.filter((w) => !cold.includes(w.id));
      const later = asked.filter((w) => cold.includes(w.id));
      const events: RunEvent[] = [
        {
          type: "start",
          run_id: `run-${d.name}`,
          dashboard: {
            name: d.name,
            version: d.version,
            spec_hash: d.spec_hash,
            draft: d.draft,
            preview: null,
          },
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
      return stream(events);
    },
  ),
];
