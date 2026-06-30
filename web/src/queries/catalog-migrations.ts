import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { catalogMigrationsApi } from "@/api/catalog-migrations";
import {
  MIGRATION_TERMINAL,
  type CatalogMigration,
} from "@/types/catalog-migration";

function isActive(status?: CatalogMigration["status"]): boolean {
  return status != null && !MIGRATION_TERMINAL.includes(status);
}

export function useCatalogMigrations(catalogId: string | null) {
  return useQuery({
    queryKey: ["catalog-migrations", catalogId],
    queryFn: () => catalogMigrationsApi.list(catalogId as string),
    enabled: !!catalogId,
    // Poll the list so a freshly started migration and its status appear live.
    refetchInterval: 3000,
  });
}

export function useCatalogMigration(
  catalogId: string | null,
  id: string | null,
) {
  return useQuery({
    queryKey: ["catalog-migration", catalogId, id],
    queryFn: () => catalogMigrationsApi.get(catalogId as string, id as string),
    enabled: !!catalogId && !!id,
    // Poll while the migration is still running; stop once it is terminal.
    refetchInterval: (query) =>
      isActive(query.state.data?.status) ? 2000 : false,
  });
}

export function useCatalogMigrationLogs(
  catalogId: string | null,
  id: string | null,
  active: boolean,
) {
  return useQuery({
    queryKey: ["catalog-migration-logs", catalogId, id],
    queryFn: () => catalogMigrationsApi.logs(catalogId as string, id as string),
    enabled: !!catalogId && !!id,
    refetchInterval: active ? 2000 : false,
  });
}

export function useStartMigration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      catalogId,
      targetStorageBackendId,
    }: {
      catalogId: string;
      targetStorageBackendId: string;
    }) => catalogMigrationsApi.start(catalogId, targetStorageBackendId),
    onSuccess: (_data, { catalogId }) => {
      qc.invalidateQueries({ queryKey: ["catalog-migrations", catalogId] });
      qc.invalidateQueries({ queryKey: ["catalogs"] });
    },
  });
}

export function useCancelMigration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ catalogId, id }: { catalogId: string; id: string }) =>
      catalogMigrationsApi.cancel(catalogId, id),
    onSuccess: (_data, { catalogId, id }) => {
      qc.invalidateQueries({ queryKey: ["catalog-migrations", catalogId] });
      qc.invalidateQueries({ queryKey: ["catalog-migration", catalogId, id] });
    },
  });
}
