import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { catalogsApi } from "@/api/catalogs";
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

export function useCreateCatalog(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
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
      catalogId,
      makeDefault,
    }: {
      catalogId: string;
      makeDefault?: boolean;
    }) => catalogsApi.attach(ws, catalogId, makeDefault),
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
