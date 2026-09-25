import { useQuery } from "@tanstack/react-query";
import { searchApi, type SearchOptions } from "@/api/search";

export function useWorkspaceSearch(ws: string, q: string) {
  const trimmed = q.trim();
  return useQuery({
    queryKey: ["workspace", ws, "search", trimmed],
    queryFn: () => searchApi.search(ws, trimmed),
    enabled: !!ws && trimmed.length >= 2,
    staleTime: 10_000,
  });
}

/** Catalog objects only, for the tree: finds tables in catalogs it has not expanded. */
export function useCatalogObjectSearch(ws: string, q: string) {
  const trimmed = q.trim();
  const opts: SearchOptions = {
    types: ["catalog", "schema", "table"],
    limit: 200,
  };
  return useQuery({
    queryKey: ["workspace", ws, "search", "objects", trimmed],
    queryFn: () => searchApi.report(ws, trimmed, opts),
    enabled: !!ws && trimmed.length >= 2,
    staleTime: 10_000,
  });
}
