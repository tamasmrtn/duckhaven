import { describe, expect, it } from "vitest";
import {
  chartData,
  formatPeriod,
  toCsv,
} from "@/features/dashboards/chartData";
import type { ChartSpec, RunColumn } from "@/types/dashboard";

const REVENUE = {
  field: "revenue",
  label: "Revenue",
  format: "currency" as const,
  currency: "EUR",
};

function spec(changes: Partial<ChartSpec>): ChartSpec {
  return {
    type: "line",
    x: { field: "week", kind: "time", grain: "week" },
    y: [REVENUE],
    series: null,
    stack: false,
    orientation: null,
    colors: {},
    limits: { rows: null, series: 8 },
    ...changes,
  };
}

const COLUMNS: RunColumn[] = [
  { name: "channel", type: "VARCHAR", role: "dimension" },
  { name: "week", type: "TIMESTAMP", role: "time" },
  { name: "revenue", type: "DECIMAL(38,2)", role: "measure" },
];

describe("chartData", () => {
  it("draws one series per key, in the slot the server gave it, oldest first", () => {
    const { data, series } = chartData(
      spec({
        series: { field: "channel", keys: ["Partner", "Web"] },
        colors: { Partner: 1, Web: 6 },
      }),
      COLUMNS,
      [
        ["Web", "2026-05-25T00:00:00", "120.50"],
        ["Partner", "2026-05-18T00:00:00", "80"],
        ["Web", "2026-05-18T00:00:00", "100"],
        // A series past the eighth was left out of the keys; so is its row.
        ["Retail", "2026-05-18T00:00:00", "999"],
      ],
    );

    expect(series.map((s) => [s.label, s.slot])).toEqual([
      ["Partner", 1],
      ["Web", 6],
    ]);
    expect(data).toEqual([
      { x: "2026-05-18T00:00:00", s0: 80, s1: 100 },
      // Partner has no value that week, which draws as a gap, not a zero.
      { x: "2026-05-25T00:00:00", s0: null, s1: 120.5 },
    ]);
  });

  it("draws one series per measure when nothing splits the chart", () => {
    const { data, series } = chartData(
      spec({
        type: "bar",
        x: { field: "country", kind: "category" },
        y: [REVENUE, { field: "orders", label: "Orders" }],
      }),
      [
        { name: "country", type: "VARCHAR", role: "dimension" },
        { name: "revenue", type: "DECIMAL(38,2)", role: "measure" },
        { name: "orders", type: "BIGINT", role: "measure" },
      ],
      [
        ["NL", "300", "4"],
        ["DE", "500", "6"],
      ],
    );

    expect(series.map((s) => [s.label, s.slot])).toEqual([
      ["Revenue", 0],
      ["Orders", 1],
    ]);
    // Categories keep the query's order.
    expect(data.map((d) => d.x)).toEqual(["NL", "DE"]);
    expect(data[1]).toEqual({ x: "DE", s0: 500, s1: 6 });
  });
});

describe("formatPeriod", () => {
  it("names a point on a time axis by its period", () => {
    expect(formatPeriod("2026-05-18T00:00:00", "week")).toBe("18 May");
    expect(formatPeriod("2026-05-18T00:00:00", "week", true)).toBe(
      "Week of 18 May 2026",
    );
    expect(formatPeriod("2026-06-01", "month")).toBe("Jun 2026");
    expect(formatPeriod("2026-04-01", "quarter")).toBe("Q2 2026");
    expect(formatPeriod("2026-01-01", "year")).toBe("2026");
    expect(formatPeriod("2026-05-18 09:30:00", null, true)).toBe(
      "18 May 2026 09:30",
    );
    expect(formatPeriod("not a date", "day")).toBe("not a date");
  });
});

describe("toCsv", () => {
  it("quotes what needs quoting and never hands a spreadsheet a formula", () => {
    const csv = toCsv(
      [
        { name: "name", type: "VARCHAR", role: null },
        { name: "amount", type: "DECIMAL(10,2)", role: null },
      ],
      [
        ['Acme, "Ltd"', "-12.50"],
        ["=HYPERLINK(1)", null],
      ],
    );
    expect(csv).toBe(
      'name,amount\r\n"Acme, ""Ltd""",-12.50\r\n\'=HYPERLINK(1),\r\n',
    );
  });
});
