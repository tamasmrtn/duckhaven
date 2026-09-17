import { useMutation, useQueryClient } from "@tanstack/react-query";
import { schemasApi, type ColumnSpec } from "@/api/schemas";

// Mutations are catalog-scoped: every cache key includes the catalog slug so a
// change in one catalog never invalidates a sibling catalog's tree.

export function useRefreshCatalogStats(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    // Probes every catalog passed in, not just the workspace default. Row
    // counts are per-catalog, so refreshing one catalog leaves every sibling
    // catalog's tables showing no count at all — which is what the tree's
    // workspace-wide button is expected to fix.
    //
    // Sequential, because each probe runs a real count(*) per table on an
    // agent the catalogs share. One catalog failing (no agent, or an agent
    // that can't serve that catalog's kind) must not stop the rest, so the
    // failures are collected and returned rather than thrown.
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
    // Re-read on settle (even on failure, e.g. no agent) so the tree reflects
    // any counts that were probed, plus schemas/tables created out-of-band.
    // Invalidate the whole catalog subtree for the workspace so every catalog
    // node refetches (the tree's top button is workspace-wide).
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
