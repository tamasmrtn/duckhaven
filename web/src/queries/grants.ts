import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { grantsApi } from "@/api/grants";
import type { AccessMode, GrantUpsertInput } from "@/types/grant";

const key = (ws: string, catalog: string) => ["grants", ws, catalog];

export function useCatalogGrants(ws: string, catalog: string | undefined) {
  return useQuery({
    queryKey: key(ws, catalog ?? ""),
    queryFn: () => grantsApi.list(ws, catalog as string),
    enabled: !!catalog,
  });
}

export function useSetAccessMode(ws: string, catalog: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (access_mode: AccessMode) =>
      grantsApi.setAccessMode(ws, catalog, access_mode),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: key(ws, catalog) });
      // The catalog list carries access_mode too (tree badge, admin toggle).
      qc.invalidateQueries({ queryKey: ["workspace", ws, "catalogs"] });
    },
  });
}

export function useUpsertGrant(ws: string, catalog: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: GrantUpsertInput) =>
      grantsApi.upsert(ws, catalog, input),
    onSuccess: () => qc.invalidateQueries({ queryKey: key(ws, catalog) }),
  });
}

export function useDeleteGrant(ws: string, catalog: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (grantId: string) => grantsApi.remove(ws, catalog, grantId),
    onSuccess: () => qc.invalidateQueries({ queryKey: key(ws, catalog) }),
  });
}
