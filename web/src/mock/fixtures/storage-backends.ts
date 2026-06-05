import type { StorageBackend } from "@/types/storage-backend";

// created_by is a user id (StorageBackendOut.created_by: uuid), not a display name.
function makeStorageBackends(): StorageBackend[] {
  return [
    {
      id: "sb-1",
      kind: "s3",
      name: "acme-prod",
      root_uri: "s3://acme-data/duckhaven/",
      uc_storage_credential_id: "uc-cred-s3",
      uc_credential_valid: true,
      workspace_count: 3,
      created_by: "u-1",
      created_at: "2026-01-01T00:00:00Z",
    },
    {
      id: "sb-2",
      kind: "adls_gen2",
      name: "research",
      root_uri: "abfss://research@acme.dfs.core.windows.net/duckhaven/",
      uc_storage_credential_id: "uc-cred-adls",
      uc_credential_valid: true,
      workspace_count: 1,
      created_by: "u-1",
      created_at: "2026-01-10T00:00:00Z",
    },
    {
      id: "sb-3",
      kind: "object_store",
      name: "home-lab",
      root_uri: "home-lab/",
      uc_storage_credential_id: null,
      uc_credential_valid: null,
      workspace_count: 1,
      created_by: "u-1",
      created_at: "2026-01-20T00:00:00Z",
    },
    {
      id: "sb-4",
      kind: "object_store",
      name: "box",
      root_uri: "",
      uc_storage_credential_id: null,
      uc_credential_valid: null,
      workspace_count: 2,
      created_by: "u-1",
      created_at: "2026-01-25T00:00:00Z",
    },
  ];
}

export let STORAGE_BACKENDS = makeStorageBackends();

export function resetStorageBackends(): void {
  STORAGE_BACKENDS = makeStorageBackends();
}
