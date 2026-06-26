import { useQuery } from "@tanstack/react-query";
import { schemasApi } from "@/api/schemas";

// Schemas/tables are scoped to a catalog. The catalog slug is part of every
// query key so a workspace's catalogs never share cache entries. Callers pass
// `undefined` to target the workspace's default catalog (legacy shim).

export function useSchemas(ws: string, catalog?: string) {
  return useQuery({
    queryKey: ["workspace", ws, "catalog", catalog, "schemas"],
    queryFn: () => schemasApi.listSchemas(ws, catalog),
    enabled: !!ws,
  });
}

export function useTables(
  ws: string,
  catalog: string | undefined,
  schema: string,
) {
  return useQuery({
    queryKey: ["workspace", ws, "catalog", catalog, "schema", schema, "tables"],
    queryFn: () => schemasApi.listTables(ws, catalog, schema),
    enabled: !!ws && !!schema,
  });
}

export function useTable(
  ws: string,
  catalog: string | undefined,
  schema: string,
  table: string,
) {
  return useQuery({
    queryKey: [
      "workspace",
      ws,
      "catalog",
      catalog,
      "schema",
      schema,
      "table",
      table,
    ],
    queryFn: () => schemasApi.getTable(ws, catalog, schema, table),
    enabled: !!ws && !!schema && !!table,
  });
}

export function useTableSample(
  ws: string,
  catalog: string | undefined,
  schema: string,
  table: string,
) {
  return useQuery({
    queryKey: [
      "workspace",
      ws,
      "catalog",
      catalog,
      "schema",
      schema,
      "table",
      table,
      "sample",
    ],
    queryFn: () => schemasApi.sampleRows(ws, catalog, schema, table),
    enabled: !!ws && !!schema && !!table,
  });
}

export function useTableSnapshots(
  ws: string,
  catalog: string | undefined,
  schema: string,
  table: string,
) {
  return useQuery({
    queryKey: [
      "workspace",
      ws,
      "catalog",
      catalog,
      "schema",
      schema,
      "table",
      table,
      "snapshots",
    ],
    queryFn: () => schemasApi.tableSnapshots(ws, catalog, schema, table),
    enabled: !!ws && !!schema && !!table,
  });
}
