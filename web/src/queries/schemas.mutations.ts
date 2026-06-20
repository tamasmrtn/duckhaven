import { useMutation, useQueryClient } from "@tanstack/react-query";
import { schemasApi, type ColumnSpec } from "@/api/schemas";

export function useRefreshCatalogStats(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => schemasApi.refreshStats(ws),
    // Re-read on settle (even on failure, e.g. no agent) so the tree reflects
    // any counts that were probed, plus schemas/tables created out-of-band.
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "schemas"] });
      qc.invalidateQueries({ queryKey: ["workspace", ws, "schema"] });
    },
  });
}

export function useRecountTable(ws: string, schema: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (table: string) => schemasApi.recountTable(ws, schema, table),
    onSettled: () => {
      qc.invalidateQueries({
        queryKey: ["workspace", ws, "schema", schema, "tables"],
      });
    },
  });
}

export function useCreateSchema(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => schemasApi.createSchema(ws, name),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "schemas"] });
    },
  });
}

export function useCreateTable(ws: string, schema: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; columns: ColumnSpec[] }) =>
      schemasApi.createTable(ws, schema, body),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: ["workspace", ws, "schema", schema, "tables"],
      });
      qc.invalidateQueries({ queryKey: ["workspace", ws, "schemas"] });
    },
  });
}

export function useDropSchema(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ schema, cascade }: { schema: string; cascade?: boolean }) =>
      schemasApi.dropSchema(ws, schema, cascade),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "schemas"] });
    },
  });
}

export function useDeleteTable(ws: string, schema: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (table: string) => schemasApi.deleteTable(ws, schema, table),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: ["workspace", ws, "schema", schema, "tables"],
      });
      qc.invalidateQueries({ queryKey: ["workspace", ws, "schemas"] });
    },
  });
}
