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
// Where a catalog keeps its metadata. Orthogonal to the storage backend: a
// catalog of either kind can sit on object_store, s3 or adls_gen2.
//   iceberg_polaris — Apache Iceberg tables in an Apache Polaris catalog.
//   ducklake        — DuckLake tables catalogued in Postgres, data in Parquet.
export type CatalogKind = "iceberg_polaris" | "ducklake";

// What a catalog's kind can do. Surfaced by the API so the UI never switches on
// `kind` itself — a third kind then changes one mapping server-side rather than
// every place that asks "is this DuckLake?".
export interface CatalogCapabilities {
  // "table" for Iceberg; "catalog" for DuckLake, whose snapshots are commits
  // against the whole catalog rather than one table.
  snapshot_granularity: "table" | "catalog";
  supports_storage_migration: boolean;
  // Whether DuckDB itself can run this kind's compaction / snapshot expiry.
  maintenance_executable: boolean;
  // Whether engines other than DuckDB can read these tables. False for DuckLake
  // — the trade-off a user makes when choosing it.
  external_engine_readable: boolean;
  supported_storage_kinds: BackendKind[];
}

export interface Catalog {
  id: string;
  slug: string;
  name: string;
  kind: CatalogKind;
  // Exactly one of these is set, per `kind`: the Polaris warehouse name, or the
  // Postgres schema holding this catalog's ducklake_* tables.
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
}
