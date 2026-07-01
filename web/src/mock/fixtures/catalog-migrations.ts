import type {
  CatalogMigration,
  CatalogMigrationEvent,
} from "@/types/catalog-migration";

let MIGRATIONS: CatalogMigration[] = [];
let EVENTS: Record<string, CatalogMigrationEvent[]> = {};

export function migrationsFor(catalogId: string): CatalogMigration[] {
  return MIGRATIONS.filter((m) => m.catalog_id === catalogId);
}

export function findMigration(id: string): CatalogMigration | undefined {
  return MIGRATIONS.find((m) => m.id === id);
}

export function eventsFor(id: string): CatalogMigrationEvent[] {
  return EVENTS[id] ?? [];
}

export function addMigration(m: CatalogMigration): void {
  MIGRATIONS.unshift(m);
  EVENTS[m.id] = [
    {
      seq: 1,
      level: "info",
      message: "Migration queued",
      created_at: new Date().toISOString(),
    },
  ];
}

export function newMigration(
  id: string,
  catalogId: string,
  targetBackendId: string,
): CatalogMigration {
  return {
    id,
    catalog_id: catalogId,
    source_storage_backend_id: "sb-1",
    target_storage_backend_id: targetBackendId,
    status: "pending",
    tables_total: 0,
    tables_done: 0,
    bytes_total: 0,
    bytes_copied: 0,
    error: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    started_at: null,
    cutover_at: null,
    finished_at: null,
  };
}

export function resetCatalogMigrations(): void {
  MIGRATIONS = [];
  EVENTS = {};
}
