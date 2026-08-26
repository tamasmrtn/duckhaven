import { get, post } from "./client";
import type { Page } from "./client";
import type {
  CatalogMigration,
  CatalogMigrationEvent,
} from "@/types/catalog-migration";

export const catalogMigrationsApi = {
  start: (catalogId: string, targetStorageBackendId: string) =>
    post<CatalogMigration>(`/catalogs/${catalogId}/migrations`, {
      target_storage_backend_id: targetStorageBackendId,
    }),

  list: (catalogId: string) =>
    get<Page<CatalogMigration>>(`/catalogs/${catalogId}/migrations`).then(
      (p) => p.items,
    ),

  get: (catalogId: string, id: string) =>
    get<CatalogMigration>(`/catalogs/${catalogId}/migrations/${id}`),

  logs: (catalogId: string, id: string, after = 0) =>
    get<CatalogMigrationEvent[]>(
      `/catalogs/${catalogId}/migrations/${id}/logs?after=${after}`,
    ),

  cancel: (catalogId: string, id: string) =>
    post<CatalogMigration>(`/catalogs/${catalogId}/migrations/${id}/cancel`),
};
