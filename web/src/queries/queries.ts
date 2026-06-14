import {
  useQuery,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { queriesApi } from "@/api/queries";

export function useQuery_(id: string | null) {
  return useQuery({
    queryKey: ["query", id],
    queryFn: () => queriesApi.get(id!),
    enabled: !!id,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "queued" || status === "running") return 300;
      return false;
    },
  });
}

export function useQueryProfile(id: string | null, enabled = true) {
  return useQuery({
    queryKey: ["query", id, "profile"],
    queryFn: () => queriesApi.profile(id!),
    enabled: !!id && enabled,
  });
}

export function useQueryRows(id: string | null, enabled = true) {
  const query = useInfiniteQuery({
    queryKey: ["query", id, "rows"],
    queryFn: ({ pageParam }) => queriesApi.rows(id!, pageParam),
    enabled: !!id && enabled,
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.cursor ?? undefined,
  });

  const pages = query.data?.pages ?? [];
  return {
    columns: pages[0]?.columns ?? [],
    rows: pages.flatMap((p) => p.rows),
    total: pages[0]?.total ?? 0,
    isLoading: query.isLoading,
    fetchNextPage: query.fetchNextPage,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
  };
}

export function useDispatchQuery(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      sql,
      agentId,
      opts,
    }: {
      sql: string;
      agentId: string;
      opts?: { memory_limit?: number; timeout?: number };
    }) => queriesApi.dispatch(ws, sql, agentId, opts),
    onSuccess: ({ id }) => {
      qc.invalidateQueries({ queryKey: ["query", id] });
    },
  });
}

export function useCancelQuery() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => queriesApi.cancel(id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: ["query", id] });
    },
  });
}

export function useSavedQueries(ws: string) {
  return useQuery({
    queryKey: ["workspace", ws, "saved-queries"],
    queryFn: () => queriesApi.listSaved(ws),
    enabled: !!ws,
  });
}

export function useSaveQuery(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      name: string;
      sql: string;
      default_agent_id?: string;
    }) => queriesApi.save(ws, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "saved-queries"] });
    },
  });
}

export function useWorkspaceQueries(ws: string) {
  return useQuery({
    queryKey: ["workspace", ws, "queries"],
    queryFn: () => queriesApi.listForWorkspace(ws),
    enabled: !!ws,
  });
}

export function useAuditLog(filters?: { user_id?: string }) {
  return useQuery({
    queryKey: ["admin", "audit", filters?.user_id ?? ""],
    queryFn: () => queriesApi.auditAll(filters),
  });
}
