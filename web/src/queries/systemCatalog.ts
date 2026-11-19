import { useQuery } from "@tanstack/react-query";
import { systemCatalogApi } from "@/api/systemCatalog";
import { useMe } from "@/queries/auth";

// Reading it takes both, as a query on it does.
const REQUIRED = ["audit:read", "queries:admin"];

/**
 * The `duckhaven` system catalog's tables, for someone who may read it. Asked only
 * of someone holding both permissions; elsewhere, and where the workspace cannot
 * read it (the server answers 404), `data` stays undefined.
 */
export function useSystemCatalog(ws: string) {
  const { data: me } = useMe();
  const mayRead = REQUIRED.every((p) => me?.permissions?.includes(p));
  return useQuery({
    queryKey: ["workspace", ws, "system-catalog"],
    queryFn: () => systemCatalogApi.get(ws),
    enabled: !!ws && mayRead,
    retry: false,
    // Fixed tables: only `available` changes, and a query says so when it fails.
    staleTime: 5 * 60_000,
  });
}
