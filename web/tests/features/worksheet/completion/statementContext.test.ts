import { describe, it, expect } from "vitest";
import {
  getCursorContext,
  referencedTables,
} from "@/features/worksheet/completion/statementContext";

// Build {text, offset} from a string with a `|` cursor marker.
function at(withCursor: string): { text: string; offset: number } {
  const offset = withCursor.indexOf("|");
  return { text: withCursor.replace("|", ""), offset };
}

describe("referencedTables", () => {
  it("parses schema.table with alias, AS-alias, and bare", () => {
    const refs = referencedTables(
      "SELECT * FROM s.sales sl JOIN s.orders AS o ON sl.id = o.sale_id",
    );
    expect(refs).toEqual([
      { schema: "s", table: "sales", alias: "sl" },
      { schema: "s", table: "orders", alias: "o" },
    ]);
  });

  it("treats a clause keyword after a table as no alias", () => {
    const refs = referencedTables("SELECT * FROM analytics.sales WHERE x = 1");
    expect(refs).toEqual([
      { schema: "analytics", table: "sales", alias: undefined },
    ]);
  });

  it("handles a bare table with an alias", () => {
    expect(referencedTables("UPDATE sales s SET x = 1")).toEqual([
      { table: "sales", alias: "s" },
    ]);
  });

  it("parses a JOIN when the first table has no alias", () => {
    expect(
      referencedTables("SELECT * FROM s.a JOIN s.b ON s.a.id = s.b.id"),
    ).toEqual([
      { schema: "s", table: "a", alias: undefined },
      { schema: "s", table: "b", alias: undefined },
    ]);
  });
});

describe("getCursorContext", () => {
  it("detects statement start", () => {
    const { text, offset } = at("|");
    expect(getCursorContext(text, offset).clause).toBe("start");
  });

  it("detects a FROM clause", () => {
    const { text, offset } = at("SELECT * FROM |");
    expect(getCursorContext(text, offset).clause).toBe("from");
  });

  it("captures a single-part qualifier", () => {
    const { text, offset } = at("SELECT s.| FROM analytics.sales s");
    const ctx = getCursorContext(text, offset);
    expect(ctx.qualifier).toEqual(["s"]);
    expect(ctx.clause).toBe("select");
  });

  it("captures a schema.table qualifier", () => {
    const { text, offset } = at("SELECT analytics.sales.| FROM analytics.sales");
    expect(getCursorContext(text, offset).qualifier).toEqual([
      "analytics",
      "sales",
    ]);
  });

  it("detects a CAST type context", () => {
    const { text, offset } = at("SELECT CAST(x AS |) FROM analytics.sales");
    expect(getCursorContext(text, offset).clause).toBe("type");
  });

  it("detects a :: cast type context", () => {
    const { text, offset } = at("SELECT x::| FROM analytics.sales");
    expect(getCursorContext(text, offset).clause).toBe("type");
  });
});
