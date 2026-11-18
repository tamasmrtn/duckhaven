import { describe, it, expect } from "vitest";
import { getCompletions } from "@/features/worksheet/completion/engine";
import { systemCatalogEntry } from "@/features/worksheet/completion/systemCatalog";
import type { CatalogSnapshot } from "@/features/worksheet/completion/types";
import { systemCatalog } from "@/mock/fixtures/systemCatalog";

const snapshot: CatalogSnapshot = {
  schemas: ["raw"],
  tablesBySchema: { raw: ["events"] },
  columnsByTable: {},
  catalogs: ["main", "duckhaven"],
  crossCatalog: { duckhaven: systemCatalogEntry(systemCatalog) },
};

function complete(withCursor: string) {
  const offset = withCursor.indexOf("|");
  const text = withCursor.replace("|", "");
  return getCompletions({ text, offset, catalog: snapshot, metadata: null });
}

describe("system catalog completion", () => {
  it("holds every schema, table and column from the start", () => {
    const entry = systemCatalogEntry(systemCatalog);

    expect(entry.schemas).toEqual(["access", "query"]);
    expect(entry.tablesBySchema).toEqual({
      access: ["audit", "grants"],
      query: ["history"],
    });
    expect(entry.columnsByTable["access.audit"]).toContainEqual({
      name: "event_time",
      type: "TIMESTAMP WITH TIME ZONE",
    });
  });

  it("completes its schemas, tables and columns", () => {
    expect(complete("SELECT * FROM duckhaven.|").map((s) => s.label)).toEqual([
      "access",
      "query",
    ]);
    expect(
      complete("SELECT * FROM duckhaven.access.|").map((s) => s.label),
    ).toEqual(["audit", "grants"]);
    expect(
      complete("SELECT duckhaven.query.history.| FROM t").map((s) => s.label),
    ).toContain("duration_ms");
  });
});
