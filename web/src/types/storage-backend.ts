export type BackendKind = "object_store" | "s3" | "adls_gen2";

export interface S3Config {
  role_arn: string;
  region: string;
  external_id?: string;
  endpoint?: string;
  path_style_access?: boolean;
}

export interface AdlsConfig {
  tenant_id: string;
  multi_tenant_app_name?: string;
  consent_url?: string;
  hierarchical?: boolean;
}

export type StorageBackendConfig = S3Config | AdlsConfig;

export interface StorageBackend {
  id: string;
  kind: BackendKind;
  name: string;
  root_uri: string;
  config: StorageBackendConfig | null;
  workspace_count: number;
  created_by: string;
  created_at: string;
}

export interface StorageBackendHealth {
  valid: boolean;
  detail: string;
}
