import type { CatalogTable } from "@/types/catalog";
import { formatBytes } from "@/utils";

/**
 * A DuckLake table whose rows all sit in the catalog database: small inserts
 * are inlined there rather than written as Parquet, so the table has rows but
 * no data files, and a size of 0 bytes that is true but misleading.
 */
export function isInlined(table: CatalogTable): boolean {
  const rows = table.row_count ?? table.row_count_estimate ?? 0;
  return (
    table.format?.toUpperCase() === "DUCKLAKE" &&
    table.size_bytes === 0 &&
    rows > 0
  );
}

export const INLINED_HINT =
  "Stored in the catalog database, not in data files: DuckLake inlines small inserts.";

/** One table's size: "Inlined", a byte count, or "—" when unknown. */
export function formatTableSize(table: CatalogTable): string {
  return isInlined(table) ? "Inlined" : formatBytes(table.size_bytes);
}

/**
 * The tables' combined size. An unknown size is not zero: `bytes` is null when
 * there are tables but none of their sizes is known, and `known` says how many
 * were counted. No tables at all is a true zero.
 */
export function totalTableSize(tables: CatalogTable[]): {
  bytes: number | null;
  known: number;
  count: number;
} {
  const sized = tables.filter((t) => t.size_bytes != null);
  return {
    bytes:
      sized.length || !tables.length
        ? sized.reduce((sum, t) => sum + (t.size_bytes ?? 0), 0)
        : null,
    known: sized.length,
    count: tables.length,
  };
}
