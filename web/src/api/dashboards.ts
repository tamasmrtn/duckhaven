import { del, get, getAllPages, patch, post, put } from "./client";
import { postSSE } from "./sse";
import type {
  Dashboard,
  DashboardPreview,
  DashboardRevision,
  DashboardRevisionSummary,
  DashboardRunRequest,
  DashboardSummary,
  DashboardValidation,
  RunEvent,
} from "@/types/dashboard";

const base = (ws: string) => `/workspaces/${ws}/dashboards`;
const one = (ws: string, name: string) =>
  `${base(ws)}/${encodeURIComponent(name)}`;
const previews = (ws: string) => `/workspaces/${ws}/dashboard-previews`;

export const dashboardsApi = {
  list: (ws: string) => getAllPages<DashboardSummary>(base(ws)),

  /** The published file, or with `draft` the draft (writers only). */
  get: (ws: string, name: string, draft = false) =>
    get<Dashboard>(`${one(ws, name)}${draft ? "?draft=true" : ""}`),

  /** A new dashboard, saved as a draft. */
  create: (ws: string, sourceText: string) =>
    post<Dashboard>(base(ws), { source_text: sourceText }),

  /**
   * Save a draft made against `baseVersion`. If the dashboard changed since,
   * this is a 409 `dashboard_conflict` carrying the current draft.
   */
  update: (ws: string, name: string, sourceText: string, baseVersion: number) =>
    patch<Dashboard>(one(ws, name), {
      source_text: sourceText,
      base_version: baseVersion,
    }),

  discardDraft: (ws: string, name: string) => del(`${one(ws, name)}/draft`),

  publish: (
    ws: string,
    name: string,
    baseVersion: number,
    message: string | null,
  ) =>
    post<Dashboard>(`${one(ws, name)}/publish`, {
      base_version: baseVersion,
      message,
    }),

  revisions: (ws: string, name: string) =>
    getAllPages<DashboardRevisionSummary>(`${one(ws, name)}/revisions`),

  revision: (ws: string, name: string, number: number) =>
    get<DashboardRevision>(`${one(ws, name)}/revisions/${number}`),

  validate: (ws: string, sourceText: string) =>
    post<DashboardValidation>(`/workspaces/${ws}/dashboard-validations`, {
      source_text: sourceText,
    }),

  /** The JSON Schema of a dashboard file. */
  schema: () => get<object>("/dashboard-schema?version=1"),

  createPreview: (ws: string, sourceText: string) =>
    post<DashboardPreview>(previews(ws), { source_text: sourceText }),

  replacePreview: (ws: string, id: string, sourceText: string) =>
    put<DashboardPreview>(`${previews(ws)}/${id}`, {
      source_text: sourceText,
    }),

  /**
   * View a dashboard as the signed-in person: yields `start`, a `widget` event
   * per widget as it is answered, and `done`. Aborting `signal` closes the
   * stream, and the server cancels the queries still running.
   */
  run: (
    ws: string,
    name: string,
    body: DashboardRunRequest,
    signal?: AbortSignal,
  ) => postSSE<RunEvent>(`${one(ws, name)}/run`, body, signal),

  /** Run a preview as the signed-in person, as `run` runs a dashboard. */
  runPreview: (
    ws: string,
    id: string,
    body: DashboardRunRequest,
    signal?: AbortSignal,
  ) => postSSE<RunEvent>(`${previews(ws)}/${id}/run`, body, signal),
};
