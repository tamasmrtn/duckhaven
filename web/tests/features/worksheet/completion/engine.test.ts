import { describe, it, expect } from "vitest";
import {
  getCompletions,
  pendingColumns,
} from "@/features/worksheet/completion/engine";
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

  it("suggests only columns inside SELECT before a prefix is typed", () => {
    const out = complete("SELECT | FROM analytics.sales");
    expect(out.map((s) => s.label)).toEqual(
      expect.arrayContaining(["id", "amount"]),
    );
    // The full function/keyword dump stays hidden until the user types.
    expect(out.some((s) => s.kind === "function")).toBe(false);
    expect(out.some((s) => s.kind === "keyword")).toBe(false);
  });

  it("surfaces functions once a prefix is typed inside SELECT", () => {
    expect(labels("SELECT su| FROM analytics.sales")).toContain("sum");
  });

  it("suggests columns inside WHERE", () => {
    const out = complete("SELECT * FROM analytics.sales WHERE |");
    const cols = out.filter((s) => s.kind === "column").map((s) => s.label);
    expect(cols).toEqual(expect.arrayContaining(["id", "amount"]));
    expect(out.some((s) => s.kind === "function")).toBe(false);
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
    // With a prefix typed, static keywords still surface without metadata.
    const out = labels("SELECT fr| FROM analytics.sales", null);
    expect(out).toContain("FROM");
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

describe("multi-table (JOIN) completion", () => {
  it("merges columns from every joined table, tagged by source", () => {
    const out = complete(
      "SELECT | FROM analytics.sales s JOIN analytics.orders o ON s.id = o.sale_id",
    );
    const cols = out.filter((s) => s.kind === "column");
    expect(cols.map((c) => c.label)).toEqual(
      expect.arrayContaining(["id", "amount", "order_id", "sale_id"]),
    );
    // The source table is shown in the detail row to disambiguate.
    expect(cols.find((c) => c.label === "id")?.detail).toContain(
      "analytics.sales",
    );
  });

  it("keeps same-named columns from different tables distinct", () => {
    const cat: CatalogSnapshot = {
      schemas: ["s"],
      tablesBySchema: { s: ["a", "b"] },
      columnsByTable: {
        "s.a": [{ name: "id", type: "BIGINT" }],
        "s.b": [{ name: "id", type: "VARCHAR" }],
      },
    };
    const text = "SELECT  FROM s.a JOIN s.b ON s.a.id = s.b.id";
    const out = getCompletions({
      text,
      offset: "SELECT ".length,
      catalog: cat,
      metadata: null,
    });
    const ids = out.filter((c) => c.kind === "column" && c.label === "id");
    expect(ids).toHaveLength(2);
  });
});

describe("pendingColumns", () => {
  it("is true when a referenced table's columns aren't loaded yet", () => {
    const cat = { ...catalog, columnsByTable: {} };
    const { text, offset } = at("SELECT | FROM analytics.sales");
    expect(pendingColumns(text, offset, cat)).toBe(true);
  });

  it("is false once the referenced table's columns are present", () => {
    const { text, offset } = at("SELECT | FROM analytics.sales");
    expect(pendingColumns(text, offset, catalog)).toBe(false);
  });

  it("is true for an alias dot whose columns aren't loaded", () => {
    const cat = { ...catalog, columnsByTable: {} };
    const { text, offset } = at("SELECT s.| FROM analytics.sales s");
    expect(pendingColumns(text, offset, cat)).toBe(true);
  });

  it("is false at statement start", () => {
    const { text, offset } = at("|");
    expect(pendingColumns(text, offset, catalog)).toBe(false);
  });
});
