import { get, del, getAllPages } from "./client";
import type { Query } from "@/types/query";
import type { SqlSession, SqlSessionStatus } from "@/types/sql-session";

export const sqlSessionsApi = {
  list: (ws: string, params?: { status?: SqlSessionStatus[] }) => {
    // `status` repeats, so it cannot go through getAllPages' flat params.
    const qs = new URLSearchParams();
    for (const s of params?.status ?? []) qs.append("status", s);
    const suffix = qs.toString();
    return getAllPages<SqlSession>(
      `/workspaces/${ws}/sql/sessions${suffix ? `?${suffix}` : ""}`,
    );
  },

  get: (id: string) => get<SqlSession>(`/sql/sessions/${id}`),

  // The session's statements in execution order (ascending), not newest-first:
  // a session is one workload, read top to bottom.
  listStatements: (id: string) =>
    getAllPages<Query>(`/sql/sessions/${id}/statements`),

  // Force-close. The same endpoint a client calls to end its own session.
  close: (id: string) => del(`/sql/sessions/${id}`),
};
