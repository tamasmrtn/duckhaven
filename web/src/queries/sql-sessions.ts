import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { sqlSessionsApi } from "@/api/sql-sessions";
import {
  LIVE_SESSION_STATUSES,
  type SqlSessionStatus,
} from "@/types/sql-session";

export function useSqlSessions(
  ws: string,
  opts?: { status?: SqlSessionStatus[]; live?: boolean },
) {
  const status = opts?.live ? LIVE_SESSION_STATUSES : opts?.status;
  return useQuery({
    queryKey: ["workspace", ws, "sql-sessions", status ?? "all"],
    queryFn: () => sqlSessionsApi.list(ws, { status }),
    enabled: !!ws,
    // Live sessions pin agent capacity, so that view must stay current.
    refetchInterval: opts?.live ? 5000 : false,
  });
}

export function useSqlSession(id: string | null) {
  return useQuery({
    queryKey: ["sql-session", id],
    queryFn: () => sqlSessionsApi.get(id!),
    enabled: !!id,
    refetchInterval: (query) =>
      query.state.data &&
      LIVE_SESSION_STATUSES.includes(query.state.data.status)
        ? 3000
        : false,
  });
}

export function useSqlSessionStatements(id: string | null, live = false) {
  return useQuery({
    queryKey: ["sql-session", id, "statements"],
    queryFn: () => sqlSessionsApi.listStatements(id!),
    enabled: !!id,
    // Poll only while the session can still produce statements.
    refetchInterval: live ? 3000 : false,
  });
}

export function useCloseSqlSession(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => sqlSessionsApi.close(id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "sql-sessions"] });
      qc.invalidateQueries({ queryKey: ["sql-session", id] });
    },
  });
}
