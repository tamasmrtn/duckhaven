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

    expect(entry.schemas).toEqual([
      "info_schema",
      "access",
      "query",
      "lineage",
      "jobs",
      "compute",
      "storage",
      "ai",
    ]);
    expect(entry.tablesBySchema).toEqual({
      info_schema: [
        "catalogs",
        "schemata",
        "tables",
        "columns",
        "routines",
        "semantic_models",
        "semantic_datasets",
        "semantic_dimensions",
        "semantic_metrics",
        "semantic_relationships",
      ],
      access: [
        "audit",
        "grants",
        "users",
        "workspaces",
        "credentials",
        "table_access",
      ],
      query: ["history", "sessions"],
      lineage: ["table_lineage", "column_lineage"],
      jobs: ["schedules"],
      compute: ["agents", "agent_events", "agent_utilization"],
      storage: ["table_health", "maintenance_operations", "backends"],
      ai: ["assistant_usage", "tool_calls"],
    });
    expect(entry.columnsByTable["access.audit"]).toContainEqual({
      name: "event_time",
      type: "TIMESTAMP WITH TIME ZONE",
    });
  });

  it("completes its schemas, tables and columns", () => {
    expect(complete("SELECT * FROM duckhaven.|").map((s) => s.label)).toEqual([
      "info_schema",
      "access",
      "query",
      "lineage",
      "jobs",
      "compute",
      "storage",
      "ai",
    ]);
    expect(
      complete("SELECT * FROM duckhaven.access.|").map((s) => s.label),
    ).toEqual([
      "audit",
      "grants",
      "users",
      "workspaces",
      "credentials",
      "table_access",
    ]);
    expect(
      complete("SELECT duckhaven.query.history.| FROM t").map((s) => s.label),
    ).toContain("duration_ms");
  });
});
