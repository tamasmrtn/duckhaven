import { get, post, patch, del, getAllPages, type Page } from "./client";
import type {
  Worksheet,
  WorksheetContent,
  WorksheetCreate,
  WorksheetMeta,
} from "@/types/worksheet";

export interface WorksheetListParams {
  status?: "open" | "closed";
  saved_query_id?: string;
  q?: string;
  sort?: "updated_at" | "title" | "position";
  dir?: "asc" | "desc";
  cursor?: string;
  limit?: number;
}

export type WorksheetUpdate = WorksheetContent &
  WorksheetMeta & { base_version?: number };

const base = (ws: string) => `/workspaces/${ws}/worksheets`;

function qs(params: WorksheetListParams): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const worksheetsApi = {
  list: (ws: string, params: WorksheetListParams = {}) =>
    get<Page<Worksheet>>(`${base(ws)}${qs(params)}`),

  // Every open tab, left to right.
  listOpen: (ws: string) =>
    getAllPages<Worksheet>(`${base(ws)}?status=open&sort=position`),

  get: (ws: string, id: string) => get<Worksheet>(`${base(ws)}/${id}`),

  create: (ws: string, body: WorksheetCreate) =>
    post<Worksheet>(base(ws), body),

  update: (ws: string, id: string, body: WorksheetUpdate) =>
    patch<Worksheet>(`${base(ws)}/${id}`, body),

  remove: (ws: string, id: string) => del(`${base(ws)}/${id}`),

  /**
   * Fire-and-forget PATCH that survives the page unloading, for the last
   * autosave on `pagehide`. Browsers cap keepalive bodies at 64 KB, so callers
   * send only what fits and prompt before unload otherwise.
   */
  updateKeepalive: (ws: string, id: string, body: WorksheetUpdate) => {
    void fetch(`/api${base(ws)}/${id}`, {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => undefined);
  },
};
