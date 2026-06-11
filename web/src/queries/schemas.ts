import { useQuery } from "@tanstack/react-query";
import { schemasApi } from "@/api/schemas";

export function useSchemas(ws: string) {
  return useQuery({
    queryKey: ["workspace", ws, "schemas"],
    queryFn: () => schemasApi.listSchemas(ws),
    enabled: !!ws,
  });
}

export function useTables(ws: string, schema: string) {
  return useQuery({
    queryKey: ["workspace", ws, "schema", schema, "tables"],
    queryFn: () => schemasApi.listTables(ws, schema),
    enabled: !!ws && !!schema,
  });
}

export function useTable(ws: string, schema: string, table: string) {
  return useQuery({
    queryKey: ["workspace", ws, "schema", schema, "table", table],
    queryFn: () => schemasApi.getTable(ws, schema, table),
    enabled: !!ws && !!schema && !!table,
  });
}

export function useTableSample(ws: string, schema: string, table: string) {
  return useQuery({
    queryKey: ["workspace", ws, "schema", schema, "table", table, "sample"],
    queryFn: () => schemasApi.sampleRows(ws, schema, table),
    enabled: !!ws && !!schema && !!table,
  });
}

export function useTableSnapshots(ws: string, schema: string, table: string) {
  return useQuery({
    queryKey: ["workspace", ws, "schema", schema, "table", table, "snapshots"],
    queryFn: () => schemasApi.tableSnapshots(ws, schema, table),
    enabled: !!ws && !!schema && !!table,
  });
}
