import { get, post, del } from "./client";
import type {
  StorageBackend,
  StorageBackendConfig,
  StorageBackendHealth,
  BackendKind,
} from "@/types/storage-backend";

export interface CreateStorageBackend {
  kind: BackendKind;
  name: string;
  root_uri: string;
  config?: StorageBackendConfig;
}

export const storageBackendsApi = {
  list: () => get<StorageBackend[]>("/admin/storage-backends"),

  create: (data: CreateStorageBackend) =>
    post<StorageBackend>("/admin/storage-backends", data),

  checkHealth: (id: string) =>
    post<StorageBackendHealth>(`/admin/storage-backends/${id}/health`, {}),

  remove: (id: string) => del(`/admin/storage-backends/${id}`),
};
