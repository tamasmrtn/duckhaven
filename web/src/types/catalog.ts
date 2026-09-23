import type { BackendKind } from "./storage-backend";

export interface TableColumn {
  position: number;
  name: string;
  type: string;
  nullable: boolean;
}

// A decoupled catalog (data domain) attached to a workspace. The same catalog
// can be attached to multiple workspaces (M:N) — `attached_workspaces` counts
// them, which gates the drop affordance.
// Where a catalog keeps its metadata. Orthogonal to the storage backend.
export type CatalogKind = "iceberg_polaris" | "ducklake";

// What a catalog's kind can do, so the UI never switches on `kind` itself.
export interface CatalogCapabilities {
  supports_storage_migration: boolean;
  external_engine_readable: boolean;
  supports_maintenance_apply?: boolean;
  supports_iceberg_export?: boolean;
  // False for DuckLake: creating a schema or table needs connected compute.
  supports_agentless_ddl?: boolean;
  supported_storage_kinds: BackendKind[];
}

export interface Catalog {
  id: string;
  slug: string;
  name: string;
  kind: CatalogKind;
  // Exactly one of these is set, per `kind`.
  polaris_name: string | null;
  metadata_schema?: string | null;
  capabilities?: CatalogCapabilities | null;
  storage_backend_id: string;
  storage_backend_kind: string;
  // Backend display name + root URI (where this catalog's data lives). Optional
  // so legacy/partial fixtures still type-check; the API always provides them.
  storage_backend_name?: string;
  storage_backend_root_uri?: string;
  created_at: string;
  is_default: boolean;
  attached_workspaces: number | null;
  // Scoped-access mode of this workspace's attachment ("open" | "scoped").
  access_mode?: "open" | "scoped";
}

export interface CatalogTable {
  name: string;
  schema_name: string;
  // The catalog slug this table belongs to (api TableOut.catalog). Optional so
  // fixtures/legacy payloads omitting it still type-check; the browser UI gets
  // the catalog from the tree context rather than the row.
  catalog?: string;
  workspace_id: string;
  row_count: number | null;
  // A free estimate from the table's current Iceberg snapshot summary (no
  // scan) — present even when `row_count` (an explicit stats refresh) is
  // still null.
  row_count_estimate: number | null;
  size_bytes: number | null;
  format: string;
  catalog_commits: boolean;
  owner: string;
  last_write_at: string | null;
  last_write_by: string | null;
  last_write_agent: string | null;
  format_version: number | null;
  snapshot_id: string | null;
  snapshot_at: string | null;
  data_file_count: number | null;
  has_deletes: boolean | null;
  columns: TableColumn[];
}

export interface CatalogSchema {
  name: string;
  catalog?: string;
  workspace_id: string;
  tables: CatalogTable[];
}

// One row of a table's Iceberg snapshot history (api SnapshotOut). Ids are
// strings: Iceberg 64-bit snapshot ids exceed JS's safe-integer range.
export interface TableSnapshot {
  snapshot_id: string;
  parent_snapshot_id: string | null;
  committed_at: string;
  operation: string | null;
  is_current: boolean;
  schema_id: number | null;
  added_records: number | null;
  deleted_records: number | null;
  total_records: number | null;
  added_data_files: number | null;
  total_data_files: number | null;
  // "catalog" for DuckLake's catalog-wide commits that touched this table.
  granularity?: "table" | "catalog";
}

// One catalog kind this deployment can create (GET /catalog-kinds).
export interface CatalogKindOption {
  kind: CatalogKind;
  label: string;
  available: boolean;
  unavailable_reason: string | null;
  capabilities: CatalogCapabilities;
}
