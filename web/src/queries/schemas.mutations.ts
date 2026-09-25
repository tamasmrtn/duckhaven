import { useMutation, useQueryClient } from "@tanstack/react-query";
import { schemasApi, type ColumnSpec } from "@/api/schemas";

// Mutations are catalog-scoped: every cache key includes the catalog slug so a
// change in one catalog never invalidates a sibling catalog's tree.

export function useRefreshCatalogStats(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    // Sequential: each probe runs a real count(*) per table on a shared agent.
    // Failures are collected so one catalog cannot stop the rest.
    mutationFn: async (catalogs: string[]) => {
      let probed = 0;
      const failed: string[] = [];
      for (const catalog of catalogs) {
        try {
          probed += (await schemasApi.refreshStats(ws, catalog)).probed;
        } catch {
          failed.push(catalog);
        }
      }
      return { probed, failed };
    },
    // Re-read on settle (even on failure) so the tree reflects any counts that
    // were probed plus any out-of-band changes.
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "catalog"] });
    },
  });
}

export function useRecountTable(ws: string, catalog: string, schema: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (table: string) =>
      schemasApi.recountTable(ws, catalog, schema, table),
    onSettled: () => {
      qc.invalidateQueries({
        queryKey: [
          "workspace",
          ws,
          "catalog",
          catalog,
          "schema",
          schema,
          "tables",
        ],
      });
    },
  });
}

export function useCreateSchema(ws: string, catalog: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => schemasApi.createSchema(ws, catalog, name),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: ["workspace", ws, "catalog", catalog, "schemas"],
      });
    },
  });
}

export function useCreateTable(ws: string, catalog: string, schema: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; columns: ColumnSpec[] }) =>
      schemasApi.createTable(ws, catalog, schema, body),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: [
          "workspace",
          ws,
          "catalog",
          catalog,
          "schema",
          schema,
          "tables",
        ],
      });
      qc.invalidateQueries({
        queryKey: ["workspace", ws, "catalog", catalog, "schemas"],
      });
    },
  });
}

export function useDropSchema(ws: string, catalog: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ schema, cascade }: { schema: string; cascade?: boolean }) =>
      schemasApi.dropSchema(ws, catalog, schema, cascade),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: ["workspace", ws, "catalog", catalog, "schemas"],
      });
    },
  });
}

export function useDeleteTable(ws: string, catalog: string, schema: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (table: string) =>
      schemasApi.deleteTable(ws, catalog, schema, table),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: [
          "workspace",
          ws,
          "catalog",
          catalog,
          "schema",
          schema,
          "tables",
        ],
      });
      qc.invalidateQueries({
        queryKey: ["workspace", ws, "catalog", catalog, "schemas"],
      });
    },
  });
}
