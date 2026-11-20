import type { SearchResult } from "@/types/search";
import type { SystemCatalog } from "@/types/systemCatalog";

/**
 * The `duckhaven` system catalog's matches for a search, shaped as the server's
 * search results. The server's search covers only the workspace's own catalogs;
 * the system catalog's tables are already listed whole (only those the caller
 * may read), so they are matched here, by name, as the server matches.
 */
export function searchSystemCatalog(
  system: SystemCatalog | undefined,
  needle: string,
): SearchResult[] {
  const q = needle.trim().toLowerCase();
  if (!system || !q || system.tables.length === 0) return [];
  const results: SearchResult[] = [];
  if (system.catalog.toLowerCase().includes(q)) {
    results.push({ type: "catalog", name: system.catalog });
  }
  const schemas = new Set<string>();
  for (const t of system.tables) {
    if (schemas.has(t.schema_name)) continue;
    schemas.add(t.schema_name);
    if (t.schema_name.toLowerCase().includes(q)) {
      results.push({
        type: "schema",
        name: t.schema_name,
        catalog: system.catalog,
      });
    }
  }
  for (const t of system.tables) {
    if (t.name.toLowerCase().includes(q)) {
      results.push({
        type: "table",
        name: t.name,
        catalog: system.catalog,
        schema_name: t.schema_name,
      });
    }
  }
  return results;
}
