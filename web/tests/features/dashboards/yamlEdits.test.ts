import { describe, expect, it } from "vitest";
import { parse } from "yaml";
import {
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
});
