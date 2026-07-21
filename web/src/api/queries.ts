import { get, post, patch, del } from "./client";
import type { Query, QueryProfile, QueryRowsPage } from "@/types/query";
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
    },
  ) => {
    const qs = new URLSearchParams();
    if (params?.all_workspaces) qs.set("all_workspaces", "true");
    if (params?.user_id) qs.set("user_id", params.user_id);
    if (params?.origin) qs.set("origin", params.origin);
    if (params?.session_id) qs.set("session_id", params.session_id);
    const suffix = qs.toString();
    return get<Query[]>(
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
    get<SavedQuery[]>(`/workspaces/${ws}/saved-queries`),

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
