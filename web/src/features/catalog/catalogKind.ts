import type { CatalogKind } from "@/types/catalog";

// Display names for a catalog's kind and the table format it implies. Kept in
// one module so the several places that show them cannot drift, the way the
// storage-backend labels did.
const KIND_LABELS: Record<CatalogKind, string> = {
  iceberg_polaris: "Apache Iceberg + Polaris",
  ducklake: "DuckLake",
};

const FORMAT_LABELS: Record<CatalogKind, string> = {
  iceberg_polaris: "Apache Iceberg",
  ducklake: "DuckLake (Parquet)",
};

/** Falls through to the raw value for a kind this build does not know, so a
 * catalog created by a newer API still renders something truthful. */
export function catalogKindLabel(kind: CatalogKind | undefined): string {
  if (!kind) return "—";
  return KIND_LABELS[kind] ?? kind;
}

export function tableFormatLabel(kind: CatalogKind | undefined): string {
  if (!kind) return "—";
  return FORMAT_LABELS[kind] ?? kind;
}
