import { useInfiniteQuery } from "@tanstack/react-query";
import { auditApi, type AuditEventsParams } from "@/api/audit";

/**
 * The access audit log, paged from the server, newest first.
 *
 * Infinite for the same reasons as query history: "Load more" appends the next
 * page, every filter is in the key so changing one starts again from page one,
 * and focusing the window does not re-request every page already read.
 */
export function useAuditEvents(params: Omit<AuditEventsParams, "cursor">) {
  const query = useInfiniteQuery({
    queryKey: ["admin", "audit", "events", params],
    queryFn: ({ pageParam }) =>
      auditApi.listEvents({ ...params, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.cursor ?? undefined,
    placeholderData: (prev) => prev,
    refetchOnWindowFocus: false,
  });

  const pages = query.data?.pages ?? [];
  return {
    items: pages.flatMap((p) => p.items),
    hasMore: pages[pages.length - 1]?.has_more ?? false,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    isError: query.isError,
    refetch: query.refetch,
    fetchNextPage: query.fetchNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
  };
}
