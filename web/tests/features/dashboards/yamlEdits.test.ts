import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import {
  addRow,
  fits,
  moveRow,
  moveWidget,
  setWidth,
} from "@/features/dashboards/editor/yamlEdits";
import { nameFrom, starterFile } from "@/features/dashboards/NewDashboardDialog";

const FILE = `version: 1
name: sales
title: Sales
rows:
  # The headline numbers.
  - widgets:
      - id: revenue # what the shop took
        type: kpi
      - id: orders
        type: kpi
        width: 4
  - widgets:
      - id: notes
        type: text
        markdown: |
          Read me first.
`;

const ids = (text: string) =>
  (parse(text) as { rows: { widgets: { id: string }[] }[] }).rows.map((r) =>
    r.widgets.map((w) => w.id),
  );

describe("arranging a dashboard file", () => {
  it("moves a widget within its row, keeping the comments", () => {
    const next = moveWidget(FILE, "revenue", 1)!;
    expect(ids(next)).toEqual([["orders", "revenue"], ["notes"]]);
    expect(next).toContain("# The headline numbers.");
    expect(next).toContain("# what the shop took");
    expect(moveWidget(next, "revenue", -1)).toBe(FILE);
  });

  it("moves a row, and its block text with it", () => {
    const next = moveRow(FILE, 1, -1)!;
    expect(ids(next)).toEqual([["notes"], ["revenue", "orders"]]);
    expect(next).toContain("markdown: |\n          Read me first.");
  });

  it("sets a width, or takes it out so the widget shares the row", () => {
    expect(setWidth(FILE, "revenue", 6)).toContain(
      "- id: revenue # what the shop took\n        type: kpi\n        width: 6",
    );
    expect(setWidth(FILE, "orders", null)).not.toContain("width");
  });

  it("changes nothing it cannot find, or in a file that does not parse", () => {
    expect(moveWidget(FILE, "revenue", -1)).toBeNull(); // already first
    expect(moveRow(FILE, 1, 1)).toBeNull(); // already last
    expect(setWidth(FILE, "nope", 6)).toBeNull();
    expect(moveWidget("rows: [unclosed", "revenue", 1)).toBeNull();
    // Duplicate keys are refused, as the server refuses them.
    expect(setWidth("title: a\ntitle: b\nrows: []\n", "x", 6)).toBeNull();
  });

  it("adds a chart in a row of its own at the end, keeping the comments", () => {
    const chart = {
      id: "revenue",
      type: "line" as const,
      title: "Revenue by month",
      width: null,
      sql: "SELECT month, revenue\nFROM sales\nORDER BY month",
    };
    const added = addRow(FILE, chart)!;
    // The id is taken, so the chart gets the next free one.
    expect(added.id).toBe("revenue_2");
    expect(ids(added.text)).toEqual([
      ["revenue", "orders"],
      ["notes"],
      ["revenue_2"],
    ]);
    expect(added.text).toContain("# what the shop took");
    // The rest of the file is as it was, and the empty width is left out.
    expect(added.text.startsWith(FILE)).toBe(true);
    expect(added.text.slice(FILE.length)).not.toContain("width");
    // Several lines of SQL are written as a block, as a person would.
    expect(added.text).toContain("sql: |-\n          SELECT month, revenue\n");
    expect(addRow(added.text, chart)!.id).toBe("revenue_3");
  });

  it("adds the first row to a file that has none", () => {
    const added = addRow("version: 1\nname: x\ntitle: X\n", {
      id: "trend",
      type: "bar",
    })!;
    expect(ids(added.text)).toEqual([["trend"]]);
    expect(added.text).toContain("rows:\n  - widgets:\n      - id: trend");
    expect(addRow("rows: [unclosed", { id: "trend" })).toBeNull();
    expect(addRow("- a list\n", { id: "trend" })).toBeNull();
    expect(addRow("rows: {}\n", { id: "trend" })).toBeNull();
  });

  it("offers a width only when the rest of the row leaves room", () => {
    expect(fits([4, 4, null], 2, 4)).toBe(true);
    expect(fits([4, 4, null], 2, 6)).toBe(false);
    // A widget's own width does not count against it.
    expect(fits([12], 0, 6)).toBe(true);
  });
});

describe("a new dashboard", () => {
  it("takes its name from the title", () => {
    expect(nameFrom("Sales overview")).toBe("sales-overview");
    expect(nameFrom("Café revenue: weekly")).toBe("cafe-revenue-weekly");
    expect(nameFrom("2026 plan")).toBe("d-2026-plan");
    expect(nameFrom("!!!")).toBe("");
    expect(nameFrom("x".repeat(80))).toHaveLength(64);
  });

  it("starts from a file that reads back with the title as typed", () => {
    const file = parse(starterFile("plan", 'Q1: "the plan" #1')) as {
      name: string;
      title: string;
      rows: { widgets: { type: string }[] }[];
    };
    expect(file.name).toBe("plan");
    expect(file.title).toBe('Q1: "the plan" #1');
    expect(file.rows[0].widgets[0].type).toBe("text");
  });

  it("starts with a chart from the assistant in place of the text widget", () => {
    const file = parse(
      starterFile("plan", "Plan", {
        id: "chart",
        type: "bar",
        title: "Orders by region",
        sql: "SELECT region, orders FROM t",
      }),
    ) as { title: string; rows: { widgets: Record<string, unknown>[] }[] };
    expect(file.title).toBe("Plan");
    expect(file.rows).toEqual([
      {
        widgets: [
          {
            id: "chart",
            type: "bar",
            title: "Orders by region",
            sql: "SELECT region, orders FROM t",
          },
        ],
      },
    ]);
  });
});
