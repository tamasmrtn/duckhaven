import type { CatalogKind } from "@/types/catalog";

const KIND_LABELS: Record<CatalogKind, string> = {
  iceberg_polaris: "Apache Iceberg + Polaris",
  ducklake: "DuckLake",
};

const FORMAT_LABELS: Record<CatalogKind, string> = {
  iceberg_polaris: "Apache Iceberg",
  ducklake: "DuckLake (Parquet)",
};

/** Falls through to the raw value for an unknown kind. */
export function catalogKindLabel(kind: CatalogKind | undefined): string {
  if (!kind) return "—";
  return KIND_LABELS[kind] ?? kind;
}

export function tableFormatLabel(kind: CatalogKind | undefined): string {
  if (!kind) return "—";
  return FORMAT_LABELS[kind] ?? kind;
}

// Spelled out because title-casing "DUCKLAKE" yields "Ducklake".
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
