export interface TableColumn {
  position: number;
  name: string;
  type: string;
  nullable: boolean;
}

export interface CatalogTable {
  name: string;
  schema_name: string;
  workspace_id: string;
  row_count: number | null;
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
