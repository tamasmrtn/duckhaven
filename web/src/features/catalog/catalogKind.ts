import type { CatalogKind } from "@/types/catalog";

// Display names for a catalog's kind and the table format it implies, in one
// module so the places that show them cannot drift.
const KIND_LABELS: Record<CatalogKind, string> = {
  iceberg_polaris: "Apache Iceberg + Polaris",
  ducklake: "DuckLake",
};

const FORMAT_LABELS: Record<CatalogKind, string> = {
  iceberg_polaris: "Apache Iceberg",
  ducklake: "DuckLake (Parquet)",
};

/** Falls through to the raw value for an unknown kind, so a catalog from a
 * newer API still renders. */
export function catalogKindLabel(kind: CatalogKind | undefined): string {
  if (!kind) return "—";
  return KIND_LABELS[kind] ?? kind;
}

export function tableFormatLabel(kind: CatalogKind | undefined): string {
  if (!kind) return "—";
  return FORMAT_LABELS[kind] ?? kind;
}

// Table formats arrive as free text ("ICEBERG" | "DUCKLAKE"); title-casing
// blindly yields "Ducklake", so spell the known ones and title-case the rest.
const FORMAT_DISPLAY: Record<string, string> = {
  ICEBERG: "Iceberg",
  DUCKLAKE: "DuckLake",
};

export function tableFormatDisplay(format: string): string {
  return (
    FORMAT_DISPLAY[format.toUpperCase()] ??
    format.charAt(0).toUpperCase() + format.slice(1).toLowerCase()
  );
}
