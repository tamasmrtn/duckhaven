import { get, post, del } from "./client";
import type { Query, QueryRowsPage } from "@/types/query";
import type { SavedQuery } from "@/types/saved-query";

export const queriesApi = {
  dispatch: (
    ws: string,
    sql: string,
    agentId: string,
    opts?: { memory_limit?: number; timeout?: number },
  ) =>
    post<Query>(`/workspaces/${ws}/queries`, {
      sql,
      agent_id: agentId,
      ...opts,
    }),

  get: (id: string) => get<Query>(`/queries/${id}`),

  rows: (id: string, cursor?: string, limit = 50) =>
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

  auditAll: (filters?: { user_id?: string }) => {
    const params = new URLSearchParams();
    if (filters?.user_id) params.set("user_id", filters.user_id);
    const qs = params.toString();
    return get<Query[]>(`/admin/audit${qs ? `?${qs}` : ""}`);
  },
};
