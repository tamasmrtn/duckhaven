import type { SystemCatalog } from "@/types/systemCatalog";
import type { CatalogEntry } from "./types";

/**
 * The `duckhaven` system catalog as completion reads a catalog. Its tables are
 * fixed and come with their columns, so the entry is whole from the start and
 * nothing is fetched as its name is typed.
 */
export function systemCatalogEntry(system: SystemCatalog): CatalogEntry {
  const entry: CatalogEntry = {
    schemas: [],
    tablesBySchema: {},
    columnsByTable: {},
  };
  for (const t of system.tables) {
    if (!entry.tablesBySchema[t.schema_name]) {
      entry.schemas.push(t.schema_name);
      entry.tablesBySchema[t.schema_name] = [];
    }
    entry.tablesBySchema[t.schema_name].push(t.name);
    entry.columnsByTable[`${t.schema_name}.${t.name}`] = t.columns.map((c) => ({
      name: c.name,
      type: c.type,
    }));
  }
  return entry;
}
