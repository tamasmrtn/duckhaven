import { useMutation, useQueryClient } from "@tanstack/react-query";
import { schemasApi, type ColumnSpec } from "@/api/schemas";

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
