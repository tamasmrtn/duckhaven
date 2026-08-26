import { get, post, patch, del } from "./client";
import type { Page } from "./client";
import type {
  QueriesPage,
  Query,
  QueryProfile,
  QueryRowsPage,
} from "@/types/query";
import type { SavedQuery } from "@/types/saved-query";

export const queriesApi = {
  dispatch: (
    ws: string,
    sql: string,
    agentId: string,
    opts?: { timeout?: number; savedQueryId?: string; catalog?: string },
  ) =>
    post<Query>(`/workspaces/${ws}/queries`, {
      sql,
      agent_id: agentId,
      timeout: opts?.timeout,
      saved_query_id: opts?.savedQueryId,
      // The worksheet's active catalog — USEd for unqualified table names.
      catalog: opts?.catalog,
    }),

  listForWorkspace: (
    ws: string,
    params?: {
      all_workspaces?: boolean;
      user_id?: string;
      // "session" / "scheduled", or "interactive" for the user-initiated runs
      // the server stores with a null origin.
      origin?: string;
      session_id?: string;
      agent_id?: string;
      // ISO timestamps bounding started_at. Open to any member for their own
      // workspace; still admin-gated together with all_workspaces.
      since?: string;
      until?: string;
      // Case-insensitive substring of the statement text.
      q?: string;
      // A full query id, or the leading part of one.
      query_id?: string;
      status?: string[];
      statement_type?: string[];
      // Reported execution time, falling back to wall clock for a run that
      // failed before reporting one.
      slower_than_ms?: number;
      sort?: "started_at" | "duration";
      dir?: "asc" | "desc";
      cursor?: string;
      limit?: number;
    },
  ) => {
    const qs = new URLSearchParams();
    if (params?.all_workspaces) qs.set("all_workspaces", "true");
    if (params?.user_id) qs.set("user_id", params.user_id);
    if (params?.origin) qs.set("origin", params.origin);
    if (params?.session_id) qs.set("session_id", params.session_id);
    if (params?.agent_id) qs.set("agent_id", params.agent_id);
    if (params?.since) qs.set("since", params.since);
    if (params?.until) qs.set("until", params.until);
    if (params?.q) qs.set("q", params.q);
    if (params?.query_id) qs.set("query_id", params.query_id);
    // Repeated keys, which is what FastAPI parses into a list. The URL bar
    // spells these comma-joined; the split happens before we get here.
    for (const s of params?.status ?? []) qs.append("status", s);
    for (const t of params?.statement_type ?? [])
      qs.append("statement_type", t);
    if (params?.slower_than_ms != null)
      qs.set("slower_than_ms", String(params.slower_than_ms));
    if (params?.sort) qs.set("sort", params.sort);
    if (params?.dir) qs.set("dir", params.dir);
    if (params?.cursor) qs.set("cursor", params.cursor);
    if (params?.limit != null) qs.set("limit", String(params.limit));
    const suffix = qs.toString();
    return get<QueriesPage>(
      `/workspaces/${ws}/queries${suffix ? `?${suffix}` : ""}`,
    );
  },

  get: (id: string) => get<Query>(`/queries/${id}`),

  profile: (id: string) => get<QueryProfile | null>(`/queries/${id}/profile`),

  rows: (id: string, cursor?: string, limit = 100) =>
    get<QueryRowsPage>(
      `/queries/${id}/rows?limit=${limit}${cursor ? `&cursor=${cursor}` : ""}`,
    ),

  cancel: (id: string) => del(`/queries/${id}`),

  listSaved: (ws: string) =>
    get<Page<SavedQuery>>(`/workspaces/${ws}/saved-queries`).then(
      (p) => p.items,
    ),

  save: (
    ws: string,
    data: { name: string; sql: string; default_agent_id?: string },
  ) => post<SavedQuery>(`/workspaces/${ws}/saved-queries`, data),

  updateSaved: (
    ws: string,
    id: string,
    data: { name?: string; sql?: string; default_agent_id?: string },
  ) => patch<SavedQuery>(`/workspaces/${ws}/saved-queries/${id}`, data),

  deleteSaved: (ws: string, id: string) =>
    del(`/workspaces/${ws}/saved-queries/${id}`),
};
