import { useInfiniteQuery } from "@tanstack/react-query";
import { worksheetsApi } from "@/api/worksheets";

// Everything worksheet-related for a workspace lives under this prefix, so one
// invalidation refreshes the open tabs and the browser alike.
export const worksheetsKey = (ws: string) =>
  ["workspace", ws, "worksheets"] as const;

// The open tabs. Owned by `useWorksheets`, which bootstraps and edits it
// optimistically; it is never refetched on focus.
export const openWorksheetsKey = (ws: string) =>
  [...worksheetsKey(ws), "open"] as const;

/** Every worksheet the caller owns here, open or closed, most recently edited first. */
export function useWorksheetBrowser(ws: string, q: string) {
  return useInfiniteQuery({
    queryKey: [...worksheetsKey(ws), "browse", q],
    queryFn: ({ pageParam }) =>
      worksheetsApi.list(ws, {
        q: q || undefined,
        sort: "updated_at",
        limit: 50,
        cursor: pageParam,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) =>
      last.has_more ? (last.cursor ?? undefined) : undefined,
    enabled: !!ws,
  });
}
