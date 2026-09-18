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

// Short badge text for the catalog tree, where the row is tight and the full
// kind label does not fit. Iceberg is deliberately unbadged: it is the default
// kind, so badging it would put a marker on every row of an Iceberg-only
// deployment to say nothing. The badge answers "which of these is not the
// default?"; the full label is on the catalog's detail page.
const KIND_BADGES: Record<CatalogKind, string | null> = {
  iceberg_polaris: null,
  ducklake: "DuckLake",
};

export function catalogKindBadge(kind: CatalogKind | undefined): string | null {
  if (!kind) return null;
  // `??` would be wrong here: a known kind maps to null deliberately (Iceberg
  // is unbadged), and `??` cannot tell that apart from a kind this build has
  // never heard of, which should fall back to its raw value.
  return kind in KIND_BADGES ? KIND_BADGES[kind] : kind;
}

// A table's format arrives from the catalog as free text ("ICEBERG" |
// "DUCKLAKE"). Title-casing it blindly yields "Ducklake", so the names we know
// are spelled here and anything else falls back to title case rather than being
// dropped — a format from a newer API still renders.
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
