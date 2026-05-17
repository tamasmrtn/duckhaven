import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { storageBackendsApi } from "@/api/storage-backends";
import type { BackendKind } from "@/types/storage-backend";

export function useStorageBackends() {
  return useQuery({
    queryKey: ["admin", "storage-backends"],
    queryFn: storageBackendsApi.list,
  });
}

export function useCreateStorageBackend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      kind: BackendKind;
      name: string;
      root_uri: string;
      uc_storage_credential_id?: string;
    }) => storageBackendsApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "storage-backends"] });
    },
  });
}

export function useDeleteStorageBackend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => storageBackendsApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "storage-backends"] });
    },
  });
}
