import { get, post, del } from "./client";
import type { StorageBackend, BackendKind } from "@/types/storage-backend";

export const storageBackendsApi = {
  list: () => get<StorageBackend[]>("/admin/storage-backends"),

  create: (data: {
    kind: BackendKind;
    name: string;
    root_uri: string;
    uc_storage_credential_id?: string;
  }) => post<StorageBackend>("/admin/storage-backends", data),

  remove: (id: string) => del(`/admin/storage-backends/${id}`),
};
