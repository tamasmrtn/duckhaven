import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { catalogsApi } from "@/api/catalogs";
import type { CatalogKind } from "@/types/catalog";
import type { AccessMode } from "@/types/grant";

export function useCatalogs(ws: string) {
  return useQuery({
    queryKey: ["workspace", ws, "catalogs"],
    queryFn: () => catalogsApi.listForWorkspace(ws),
    enabled: !!ws,
  });
}

export function useAllCatalogs() {
  return useQuery({
    queryKey: ["catalogs"],
    queryFn: () => catalogsApi.listAll(),
  });
}

export function useCatalogKinds() {
  return useQuery({
    queryKey: ["catalog-kinds"],
    queryFn: () => catalogsApi.listKinds(),
    // Kinds change only on an API restart with a different flag.
    staleTime: 5 * 60 * 1000,
  });
}

export function useCreateCatalog(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      kind?: CatalogKind;
      storage_backend_id?: string;
      access_mode?: AccessMode;
    }) => catalogsApi.create(ws, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "catalogs"] });
      qc.invalidateQueries({ queryKey: ["catalogs"] });
    },
  });
}

export function useAttachCatalog(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      catalog,
      makeDefault,
    }: {
      catalog: string;
      makeDefault?: boolean;
    }) => catalogsApi.attach(ws, catalog, makeDefault),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "catalogs"] });
      qc.invalidateQueries({ queryKey: ["catalogs"] });
    },
  });
}

export function useDetachCatalog(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (catalog: string) => catalogsApi.detach(ws, catalog),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "catalogs"] });
      qc.invalidateQueries({ queryKey: ["catalogs"] });
    },
  });
}

export function useDropCatalog(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (catalogId: string) => catalogsApi.drop(catalogId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "catalogs"] });
      qc.invalidateQueries({ queryKey: ["catalogs"] });
    },
  });
}
