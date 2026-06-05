export type BackendKind = "object_store" | "s3" | "adls_gen2";

export interface StorageBackend {
  id: string;
  kind: BackendKind;
  name: string;
  root_uri: string;
  uc_storage_credential_id: string | null;
  uc_credential_valid: boolean | null;
  workspace_count: number;
  created_by: string;
  created_at: string;
}
