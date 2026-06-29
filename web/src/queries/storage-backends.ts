import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  storageBackendsApi,
  type CreateStorageBackend,
} from "@/api/storage-backends";

export function useStorageBackends() {
  return useQuery({
    queryKey: ["admin", "storage-backends"],
    queryFn: storageBackendsApi.list,
  });
}

export function useCreateStorageBackend() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateStorageBackend) => storageBackendsApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "storage-backends"] });
    },
  });
}

export function useCheckStorageBackendHealth() {
  return useMutation({
    mutationFn: (id: string) => storageBackendsApi.checkHealth(id),
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
