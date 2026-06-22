import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueries, useQueryClient } from "@tanstack/react-query";
import { schemasApi } from "@/api/schemas";
import { useSchemas } from "@/queries/schemas";
import { useSqlMetadata } from "@/queries/sqlMetadata";
import type { CatalogTable } from "@/types/catalog";
import type { CatalogSnapshot, SnapshotColumn } from "./types";
import {
  type ColumnRef,
  retriggerSuggest,
  updateCompletionContext,
} from "./provider";

// Feeds the worksheet's Monaco completion providers with the workspace catalog
// (schemas + table names eagerly; columns fetched lazily on reference) and the
// DuckDB function/keyword/type dictionary. Mounted once by WorksheetPage.
export function useSqlCompletion(ws: string): void {
  const qc = useQueryClient();
  const schemasQuery = useSchemas(ws);
  const metadataQuery = useSqlMetadata(ws);

  const schemaNames = useMemo(
    () => (schemasQuery.data ?? []).map((s) => s.name),
    [schemasQuery.data],
  );

  const tableQueries = useQueries({
    queries: schemaNames.map((schema) => ({
      queryKey: ["workspace", ws, "schema", schema, "tables"],
      queryFn: () => schemasApi.listTables(ws, schema),
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

  const snapshot = useMemo<CatalogSnapshot>(
    () => ({ schemas: schemaNames, tablesBySchema, columnsByTable }),
    [schemaNames, tablesBySchema, columnsByTable],
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
          queryKey: ["workspace", ws, "schema", schema, "table", ref.table],
          queryFn: () => schemasApi.getTable(ws, schema, ref.table),
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
    [qc, ws],
  );

  useEffect(() => {
    updateCompletionContext({
      snapshot,
      metadata: metadataQuery.data ?? null,
      ensureColumns,
    });
  }, [snapshot, metadataQuery.data, ensureColumns]);

  // Refresh an open suggest widget when lazily-fetched columns land, so a
  // held-open Ctrl+Space fills in without the user having to type.
  useEffect(() => {
    retriggerSuggest();
  }, [columnsByTable]);

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
