import { describe, it, expect } from "vitest";
import { getCompletions } from "@/features/worksheet/completion/engine";
import type { CatalogSnapshot } from "@/features/worksheet/completion/types";
import type { SqlMetadata } from "@/types/sqlMetadata";

const catalog: CatalogSnapshot = {
  schemas: ["analytics", "raw"],
  tablesBySchema: { analytics: ["sales", "orders"], raw: ["events"] },
  columnsByTable: {
    "analytics.sales": [
      { name: "id", type: "BIGINT" },
      { name: "amount", type: "DOUBLE" },
    ],
    "analytics.orders": [
      { name: "order_id", type: "BIGINT" },
      { name: "sale_id", type: "BIGINT" },
    ],
  },
};

const metadata: SqlMetadata = {
  functions: [
    {
      name: "sum",
      type: "aggregate",
      return_type: "DOUBLE",
      signature: "sum(x DOUBLE) → DOUBLE",
      examples: "sum(amount)",
    },
  ],
  keywords: [
    { name: "select", category: "reserved" },
    { name: "where", category: "reserved" },
  ],
  types: [
    { name: "INTEGER", category: "NUMERIC" },
    { name: "VARCHAR", category: "STRING" },
  ],
};

function at(withCursor: string): { text: string; offset: number } {
  const offset = withCursor.indexOf("|");
  return { text: withCursor.replace("|", ""), offset };
}

function complete(withCursor: string, md: SqlMetadata | null = metadata) {
  const { text, offset } = at(withCursor);
  return getCompletions({ text, offset, catalog, metadata: md });
}

function labels(withCursor: string, md: SqlMetadata | null = metadata) {
  return complete(withCursor, md).map((s) => s.label);
}

describe("getCompletions", () => {
  it("suggests schemas and tables after FROM", () => {
    const out = complete("SELECT * FROM |");
    const byKind = (k: string) =>
      out.filter((s) => s.kind === k).map((s) => s.label);
    expect(byKind("schema")).toEqual(["analytics", "raw"]);
    expect(byKind("table")).toEqual(
      expect.arrayContaining(["sales", "orders", "events"]),
    );
  });

  it("suggests tables in a schema after `schema.`", () => {
    const out = complete("SELECT * FROM analytics.|");
    expect(out.map((s) => s.label)).toEqual(["sales", "orders"]);
    expect(out.every((s) => s.kind === "table")).toBe(true);
  });

  it("resolves alias columns after `alias.`", () => {
    const out = complete("SELECT s.| FROM analytics.sales s");
    expect(out.map((s) => s.label)).toEqual(["id", "amount"]);
    expect(out.every((s) => s.kind === "column")).toBe(true);
  });

  it("resolves bare-table columns after `table.`", () => {
    const out = complete("SELECT sales.| FROM analytics.sales");
    expect(out.map((s) => s.label)).toEqual(["id", "amount"]);
  });

  it("suggests columns, functions and keywords inside SELECT", () => {
    const out = labels("SELECT | FROM analytics.sales");
    expect(out).toEqual(expect.arrayContaining(["id", "amount", "sum", "SELECT"]));
  });

  it("suggests columns inside WHERE", () => {
    const out = labels("SELECT * FROM analytics.sales WHERE |");
    expect(out).toEqual(expect.arrayContaining(["amount", "sum"]));
  });

  it("suggests data types in a CAST", () => {
    const out = complete("SELECT CAST(x AS |) FROM analytics.sales");
    expect(out.map((s) => s.label)).toEqual(
      expect.arrayContaining(["INTEGER", "VARCHAR"]),
    );
    expect(out.every((s) => s.kind === "type")).toBe(true);
  });

  it("suggests statement-start keywords at the start", () => {
    const out = complete("|");
    expect(out.every((s) => s.kind === "keyword")).toBe(true);
    expect(out.map((s) => s.label)).toEqual(expect.arrayContaining(["SELECT"]));
  });

  it("falls back to static keywords when metadata is not loaded", () => {
    const out = labels("SELECT | FROM analytics.sales", null);
    // Columns still resolve from the catalog; static keywords still appear.
    expect(out).toEqual(expect.arrayContaining(["amount", "FROM"]));
    // No function suggestions without metadata.
    expect(out).not.toContain("sum");
  });

  it("ranks prefix matches first and dedups", () => {
    const out = complete("SELECT am| FROM analytics.sales");
    expect(out[0].label).toBe("amount");
    const labelsOut = out.map((s) => s.label);
    expect(new Set(labelsOut).size).toBe(labelsOut.length);
  });
});
