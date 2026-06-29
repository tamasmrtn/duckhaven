import { http, HttpResponse } from "msw";
import { SCHEDULES, SCHEDULE_RUNS } from "../fixtures/schedules";
import { findWorkspace } from "../fixtures/workspaces";
import { nextId } from "../lib/seed";
import { httpError, validationError } from "../lib/errors";
import type { Schedule } from "@/types/schedule";

// A naive 5-field cron check mirroring the backend's croniter validation enough
// for the UI: five space-separated fields, each a cron-ish token.
function cronValid(expr: string): boolean {
  const fields = expr.trim().split(/\s+/);
  if (fields.length !== 5) return false;
  return fields.every((f) => /^[\d*/,-]+$/.test(f));
}

function nextRunFrom(now = new Date()): string {
  // The mock doesn't compute real cron times; just hand back a near-future stamp.
  return new Date(now.getTime() + 3600_000).toISOString();
}

export const scheduleHandlers = [
  http.get("/api/workspaces/:ws/schedules", ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const url = new URL(request.url);
    const savedQueryId = url.searchParams.get("saved_query_id");
    return HttpResponse.json(
      SCHEDULES.filter(
        (s) =>
          s.workspace_id === ws.id &&
          (!savedQueryId || s.saved_query_id === savedQueryId),
      ),
    );
  }),

  http.post("/api/workspaces/:ws/schedules", async ({ params, request }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const body = (await request.json()) as {
      saved_query_id: string;
      cron: string;
      enabled?: boolean;
      agent_id?: string | null;
      job_type?: string;
    };
    if (!cronValid(body.cron)) {
      return validationError(
        "invalid_cron",
        `Invalid cron expression: ${body.cron}`,
      );
    }
    const enabled = body.enabled ?? true;
    const schedule: Schedule = {
      id: nextId("sch"),
      workspace_id: ws.id,
      job_type: body.job_type ?? "saved_query",
      saved_query_id: body.saved_query_id,
      agent_id: body.agent_id ?? null,
      cron: body.cron,
      enabled,
      next_run_at: enabled ? nextRunFrom() : null,
      last_run_at: null,
      last_run_query_id: null,
      created_at: new Date().toISOString(),
    };
    SCHEDULES.push(schedule);
    return HttpResponse.json(schedule, { status: 201 });
  }),

  http.patch(
    "/api/workspaces/:ws/schedules/:id",
    async ({ params, request }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const schedule = SCHEDULES.find(
        (s) => s.id === params.id && s.workspace_id === ws.id,
      );
      if (!schedule) return httpError(404, "Schedule not found");
      const body = (await request.json()) as {
        cron?: string;
        enabled?: boolean;
        agent_id?: string | null;
      };
      if (body.cron !== undefined) {
        if (!cronValid(body.cron)) {
          return validationError(
            "invalid_cron",
            `Invalid cron expression: ${body.cron}`,
          );
        }
        schedule.cron = body.cron;
      }
      if (body.agent_id !== undefined) schedule.agent_id = body.agent_id;
      if (body.enabled !== undefined) schedule.enabled = body.enabled;
      schedule.next_run_at = schedule.enabled ? nextRunFrom() : null;
      return HttpResponse.json(schedule);
    },
  ),

  http.delete("/api/workspaces/:ws/schedules/:id", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const idx = SCHEDULES.findIndex(
      (s) => s.id === params.id && s.workspace_id === ws.id,
    );
    if (idx === -1) return httpError(404, "Schedule not found");
    SCHEDULES.splice(idx, 1);
    return new HttpResponse(null, { status: 204 });
  }),

  http.get("/api/workspaces/:ws/schedules/:id/runs", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    const runs = SCHEDULE_RUNS.filter((q) => q.workspace_id === ws.id)
      .slice()
      .sort((a, b) => b.started_at.localeCompare(a.started_at));
    return HttpResponse.json(runs);
  }),
];
