import { describe, expect, it } from "vitest";
import { searchSystemCatalog } from "@/features/catalog/systemSearch";
import type { SystemCatalog } from "@/types/systemCatalog";

const system: SystemCatalog = {
  catalog: "duckhaven",
  available: true,
  tables: [
    { schema_name: "access", name: "audit", description: "", permission: "audit:read", columns: [] },
    { schema_name: "access", name: "table_access", description: "", permission: "audit:read", columns: [] },
    { schema_name: "query", name: "history", description: "", permission: "queries:admin", columns: [] },
  ],
};

describe("searchSystemCatalog", () => {
  it("matches tables by name, with their catalog and schema", () => {
    expect(searchSystemCatalog(system, "hist")).toEqual([
      { type: "table", name: "history", catalog: "duckhaven", schema_name: "query" },
    ]);
  });

  it("matches a schema once, however many tables it has", () => {
    expect(searchSystemCatalog(system, "access")).toEqual([
      { type: "schema", name: "access", catalog: "duckhaven" },
      { type: "table", name: "table_access", catalog: "duckhaven", schema_name: "access" },
    ]);
  });

  it("matches the catalog itself, ignoring case and surrounding spaces", () => {
    expect(searchSystemCatalog(system, "  DuckHaven ")).toEqual([
      { type: "catalog", name: "duckhaven" },
    ]);
  });

  it("finds nothing without a listing, a needle or a table to read", () => {
    expect(searchSystemCatalog(undefined, "audit")).toEqual([]);
    expect(searchSystemCatalog(system, "  ")).toEqual([]);
    expect(searchSystemCatalog({ ...system, tables: [] }, "duckhaven")).toEqual([]);
  });
});
