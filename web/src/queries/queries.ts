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
    columnSchema: pages[0]?.column_schema ?? null,
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
      opts?: {
        memory_limit?: number;
        timeout?: number;
        savedQueryId?: string;
        catalog?: string;
      };
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

export function useUpdateSavedQuery(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: string;
      data: { name?: string; sql?: string; default_agent_id?: string };
    }) => queriesApi.updateSaved(ws, id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "saved-queries"] });
    },
  });
}

export function useDeleteSavedQuery(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => queriesApi.deleteSaved(ws, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "saved-queries"] });
    },
  });
}

export interface WorkspaceQueriesOptions {
  all_workspaces?: boolean;
  user_id?: string;
  origin?: string;
  session_id?: string;
  agent_id?: string;
  since?: string;
  until?: string;
  q?: string;
  query_id?: string;
  status?: string[];
  statement_type?: string[];
  slower_than_ms?: number;
  sort?: "started_at" | "duration";
  dir?: "asc" | "desc";
  limit?: number;
  /**
   * Hold the request until the caller has what it needs to scope it.
   *
   * History defaults to the signed-in user's runs, which it cannot ask for
   * until `useMe` resolves. Without this it would fire once unscoped — a wasted
   * round trip that briefly shows other people's queries.
   */
  enabled?: boolean;
}

/**
 * Query history, paged from the server.
 *
 * Infinite rather than a plain query because the list is genuinely paged now:
 * "Load more" appends the next page instead of refetching a bigger one. Every
 * filter is in the key, so changing one resets to page one rather than
 * appending the new filter's first page onto the old filter's rows.
 */
export function useWorkspaceQueries(
  ws: string,
  opts?: WorkspaceQueriesOptions,
) {
  const query = useInfiniteQuery({
    queryKey: [
      "workspace",
      ws,
      "queries",
      opts?.all_workspaces ? "all" : "ws",
      opts?.user_id ?? "",
      opts?.origin ?? "",
      opts?.session_id ?? "",
      opts?.agent_id ?? "",
      opts?.since ?? "",
      opts?.until ?? "",
      opts?.q ?? "",
      opts?.query_id ?? "",
      (opts?.status ?? []).join(","),
      (opts?.statement_type ?? []).join(","),
      opts?.slower_than_ms ?? "",
      opts?.sort ?? "",
      opts?.dir ?? "",
      opts?.limit ?? "",
    ],
    queryFn: ({ pageParam }) =>
      queriesApi.listForWorkspace(ws, { ...opts, cursor: pageParam }),
    enabled: !!ws && (opts?.enabled ?? true),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.cursor ?? undefined,
    // Hold the previous filter's rows while the next load lands, so changing a
    // filter dims the table instead of collapsing it to skeletons.
    placeholderData: (prev) => prev,
    // Refetching an infinite query re-requests *every* loaded page. With the
    // app-wide 30s staleTime, tabbing away and back after reading ten pages
    // would fire ten keyset queries nobody asked for. History is not a live
    // dashboard and has an explicit Refresh; that button still refreshes all
    // loaded pages, which is what someone pressing it is asking for.
    //
    // `maxPages` would also bound it, but by dropping pages out of the cache —
    // rows the reader had already loaded would vanish from under them.
    refetchOnWindowFocus: false,
  });

  const pages = query.data?.pages ?? [];
  return {
    items: pages.flatMap((p) => p.items),
    hasMore: pages[pages.length - 1]?.has_more ?? false,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
    fetchNextPage: query.fetchNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
  };
}
