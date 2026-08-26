import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { schemasApi } from "@/api/schemas";
import { useSchemas } from "@/queries/schemas";
import { useCatalogs } from "@/queries/catalogs";
import { useSqlMetadata } from "@/queries/sqlMetadata";
import type { CatalogTable } from "@/types/catalog";
import type { CatalogEntry, CatalogSnapshot, SnapshotColumn } from "./types";
import {
  type ColumnRef,
  type RowCountInfo,
  retriggerSuggest,
  updateCompletionContext,
} from "./provider";

// Feeds the worksheet's Monaco completion providers with the active catalog's
// schemas + table names (eagerly; columns fetched lazily on reference) and the
// DuckDB function/keyword/type dictionary. Mounted once by WorksheetPage; the
// catalog is the worksheet's active catalog (undefined → default catalog).
export function useSqlCompletion(ws: string, catalog: string): void {
  const qc = useQueryClient();
  const schemasQuery = useSchemas(ws, catalog);
  const metadataQuery = useSqlMetadata(ws);
  const catalogsQuery = useCatalogs(ws);

  // Every catalog attached to the workspace, including the active one (so a
  // fully-qualified self-reference like `activeCatalog.schema.table` still
  // resolves) — cheap, eager, already fetched for the worksheet's catalog
  // switcher.
  const catalogNames = useMemo(
    () => (catalogsQuery.data ?? []).map((c) => c.slug),
    [catalogsQuery.data],
  );

  const schemaNames = useMemo(
    () => (schemasQuery.data ?? []).map((s) => s.name),
    [schemasQuery.data],
  );

  const tableQueries = useQueries({
    queries: schemaNames.map((schema) => ({
      queryKey: [
        "workspace",
        ws,
        "catalog",
        catalog,
        "schema",
        schema,
        "tables",
      ],
      queryFn: () => schemasApi.listTables(ws, catalog, schema),
      enabled: !!ws && !!schema,
    })),
  });

  // Columns fetched lazily, keyed by "schema.table".
  const [columnsByTable, setColumnsByTable] = useState<
    Record<string, SnapshotColumn[]>
  >({});

  const tablesBySchema = useMemo(() => {
    const out: Record<string, string[]> = {};
    schemaNames.forEach((schema, i) => {
      out[schema] = (tableQueries[i]?.data ?? []).map((t) => t.name);
    });
    return out;
  }, [schemaNames, tableQueries]);

  // Non-active-catalog data, lazily loaded one level at a time as a
  // `catalog.`/`catalog.schema.`/`catalog.schema.table.` qualifier is typed.
  const [crossSchemas, setCrossSchemas] = useState<Record<string, string[]>>(
    {},
  );
  const [crossTables, setCrossTables] = useState<
    Record<string, Record<string, string[]>>
  >({});
  const [crossColumns, setCrossColumns] = useState<
    Record<string, Record<string, SnapshotColumn[]>>
  >({});
  const requestedCrossSchemas = useRef<Set<string>>(new Set());
  const requestedCrossTables = useRef<Set<string>>(new Set());
  const requestedCrossColumns = useRef<Set<string>>(new Set());

  const ensureCrossCatalogSchemas = useCallback(
    (catalogName: string) => {
      if (requestedCrossSchemas.current.has(catalogName)) return;
      requestedCrossSchemas.current.add(catalogName);
      qc.ensureQueryData({
        queryKey: ["workspace", ws, "catalog", catalogName, "schemas"],
        queryFn: () => schemasApi.listSchemas(ws, catalogName),
      })
        .then((schemas) => {
          setCrossSchemas((prev) => ({
            ...prev,
            [catalogName]: schemas.map((s) => s.name),
          }));
        })
        .catch(() => requestedCrossSchemas.current.delete(catalogName));
    },
    [qc, ws],
  );

  const ensureCrossCatalogTables = useCallback(
    (catalogName: string, schema: string) => {
      const key = `${catalogName}.${schema}`;
      if (requestedCrossTables.current.has(key)) return;
      requestedCrossTables.current.add(key);
      qc.ensureQueryData({
        queryKey: [
          "workspace",
          ws,
          "catalog",
          catalogName,
          "schema",
          schema,
          "tables",
        ],
        queryFn: () => schemasApi.listTables(ws, catalogName, schema),
      })
        .then((tables) => {
          setCrossTables((prev) => ({
            ...prev,
            [catalogName]: {
              ...prev[catalogName],
              [schema]: tables.map((t) => t.name),
            },
          }));
        })
        .catch(() => requestedCrossTables.current.delete(key));
    },
    [qc, ws],
  );

  const ensureCrossCatalogColumns = useCallback(
    (catalogName: string, schema: string, table: string) => {
      const tableKey = `${schema}.${table}`;
      const key = `${catalogName}.${tableKey}`;
      if (requestedCrossColumns.current.has(key)) return;
      requestedCrossColumns.current.add(key);
      qc.ensureQueryData<CatalogTable>({
        queryKey: [
          "workspace",
          ws,
          "catalog",
          catalogName,
          "schema",
          schema,
          "table",
          table,
        ],
        queryFn: () => schemasApi.getTable(ws, catalogName, schema, table),
      })
        .then((t) => {
          setCrossColumns((prev) => ({
            ...prev,
            [catalogName]: {
              ...prev[catalogName],
              [tableKey]: t.columns.map((c) => ({
                name: c.name,
                type: c.type,
              })),
            },
          }));
        })
        .catch(() => requestedCrossColumns.current.delete(key));
    },
    [qc, ws],
  );

  // The active catalog's own slug, aliased into `crossCatalog` so a
  // fully-qualified self-reference (`activeCatalog.schema.table`) resolves
  // from already-loaded data instead of triggering a redundant fetch.
  const crossCatalog = useMemo<Record<string, CatalogEntry>>(() => {
    const out: Record<string, CatalogEntry> = {};
    for (const name of Object.keys(crossSchemas)) {
      out[name] = {
        schemas: crossSchemas[name] ?? [],
        tablesBySchema: crossTables[name] ?? {},
        columnsByTable: crossColumns[name] ?? {},
      };
    }
    if (catalog) {
      out[catalog] = { schemas: schemaNames, tablesBySchema, columnsByTable };
    }
    return out;
  }, [
    crossSchemas,
    crossTables,
    crossColumns,
    catalog,
    schemaNames,
    tablesBySchema,
    columnsByTable,
  ]);

  const snapshot = useMemo<CatalogSnapshot>(
    () => ({
      schemas: schemaNames,
      tablesBySchema,
      columnsByTable,
      catalogs: catalogNames,
      crossCatalog,
    }),
    [schemaNames, tablesBySchema, columnsByTable, catalogNames, crossCatalog],
  );

  // Stable lookups for ensureColumns so it never goes stale in the provider ref.
  const tablesRef = useRef(tablesBySchema);
  useEffect(() => {
    tablesRef.current = tablesBySchema;
  }, [tablesBySchema]);
  const requested = useRef<Set<string>>(new Set());

  const ensureColumns = useCallback(
    (refs: ColumnRef[]) => {
      for (const ref of refs) {
        const schema =
          ref.schema ??
          Object.keys(tablesRef.current).find((s) =>
            tablesRef.current[s].includes(ref.table),
          );
        if (!schema) continue;
        const key = `${schema}.${ref.table}`;
        if (requested.current.has(key)) continue;
        requested.current.add(key);
        qc.ensureQueryData<CatalogTable>({
          queryKey: [
            "workspace",
            ws,
            "catalog",
            catalog,
            "schema",
            schema,
            "table",
            ref.table,
          ],
          queryFn: () => schemasApi.getTable(ws, catalog, schema, ref.table),
        })
          .then((table) => {
            setColumnsByTable((prev) =>
              key in prev
                ? prev
                : {
                    ...prev,
                    [key]: table.columns.map((c) => ({
                      name: c.name,
                      type: c.type,
                    })),
                  },
            );
          })
          .catch(() => requested.current.delete(key));
      }
    },
    [qc, ws, catalog],
  );

  // Row count for the completion-detail panel, fetched lazily one table at a
  // time (see `resolveCompletionItem` in provider.ts) — same query key as
  // `ensureColumns`/`useTable`, so it shares a cache entry with the catalog
  // tree's click-expand and hover-preview fetches.
  const [rowCountByTable, setRowCountByTable] = useState<
    Record<string, RowCountInfo | null>
  >({});
  const requestedDetail = useRef<Set<string>>(new Set());

  const ensureTableDetail = useCallback(
    (schema: string, table: string) => {
      const key = `${schema}.${table}`;
      if (requestedDetail.current.has(key)) return;
      requestedDetail.current.add(key);
      qc.ensureQueryData<CatalogTable>({
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
      })
        .then((t) => {
          const info: RowCountInfo | null =
            t.row_count != null
              ? { count: t.row_count, exact: true }
              : t.row_count_estimate != null
                ? { count: t.row_count_estimate, exact: false }
                : null;
          setRowCountByTable((prev) =>
            key in prev ? prev : { ...prev, [key]: info },
          );
        })
        .catch(() => requestedDetail.current.delete(key));
    },
    [qc, ws, catalog],
  );

  useEffect(() => {
    updateCompletionContext({
      snapshot,
      metadata: metadataQuery.data ?? null,
      ensureColumns,
      ensureCrossCatalogSchemas,
      ensureCrossCatalogTables,
      ensureTableDetail,
      rowCountByTable,
      ensureCrossCatalogColumns,
    });
  }, [
    snapshot,
    metadataQuery.data,
    ensureColumns,
    ensureCrossCatalogSchemas,
    ensureCrossCatalogTables,
    ensureCrossCatalogColumns,
    ensureTableDetail,
    rowCountByTable,
  ]);

  // Refresh an open suggest widget when lazily-fetched columns land, so a
  // held-open Ctrl+Space fills in without the user having to type.
  useEffect(() => {
    retriggerSuggest();
  }, [
    columnsByTable,
    crossSchemas,
    crossTables,
    crossColumns,
    rowCountByTable,
  ]);

  // When the catalog is refetched (e.g. after a DDL run invalidates it), drop
  // the lazy column cache so altered/created/dropped tables re-fetch fresh
  // columns on the next completion instead of serving stale ones.
  const catalogVersion = useMemo(
    () =>
      [
        schemasQuery.dataUpdatedAt,
        ...tableQueries.map((q) => q.dataUpdatedAt),
      ].join(":"),
    [schemasQuery.dataUpdatedAt, tableQueries],
  );
  const prevCatalogVersion = useRef(catalogVersion);
  useEffect(() => {
    if (prevCatalogVersion.current === catalogVersion) return;
    prevCatalogVersion.current = catalogVersion;
    requested.current.clear();
    setColumnsByTable({});
  }, [catalogVersion]);
}
