import { get, post, del } from "./client";
import type { Query, QueryProfile, QueryRowsPage } from "@/types/query";
import type { SavedQuery } from "@/types/saved-query";

export const queriesApi = {
  dispatch: (
    ws: string,
    sql: string,
    agentId: string,
    opts?: { timeout?: number },
  ) =>
    post<Query>(`/workspaces/${ws}/queries`, {
      sql,
      agent_id: agentId,
      ...opts,
    }),

  listForWorkspace: (
    ws: string,
    params?: { all_workspaces?: boolean; user_id?: string },
  ) => {
    const qs = new URLSearchParams();
    if (params?.all_workspaces) qs.set("all_workspaces", "true");
    if (params?.user_id) qs.set("user_id", params.user_id);
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
};
