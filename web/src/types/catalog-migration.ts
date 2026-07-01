// A run of moving a catalog's Iceberg data from one storage backend to another.
// Mirrors the API `CatalogMigrationOut` (api/schemas/catalog_migration.py).
export type MigrationStatus =
  | "pending"
  | "copying"
  | "verifying"
  | "cutover"
  | "completed"
  | "failed"
  | "cancelled";

export const MIGRATION_TERMINAL: MigrationStatus[] = [
  "completed",
  "failed",
  "cancelled",
];

export interface CatalogMigrationTable {
  schema_name: string;
  table_name: string;
  status: string;
  bytes_copied: number;
  error: string | null;
}

export interface CatalogMigration {
  id: string;
  catalog_id: string;
  source_storage_backend_id: string;
  target_storage_backend_id: string;
  status: MigrationStatus;
  tables_total: number;
  tables_done: number;
  bytes_total: number;
  bytes_copied: number;
  error: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  cutover_at: string | null;
  finished_at: string | null;
  tables?: CatalogMigrationTable[] | null;
}

export interface CatalogMigrationEvent {
  seq: number;
  level: string;
  message: string;
  created_at: string;
}
