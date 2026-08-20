import { useQuery } from "@tanstack/react-query";
import { searchApi } from "@/api/search";

export function useWorkspaceSearch(ws: string, q: string) {
  const trimmed = q.trim();
  return useQuery({
    queryKey: ["workspace", ws, "search", trimmed],
    queryFn: () => searchApi.search(ws, trimmed),
    enabled: !!ws && trimmed.length >= 2,
    staleTime: 10_000,
  });
}
