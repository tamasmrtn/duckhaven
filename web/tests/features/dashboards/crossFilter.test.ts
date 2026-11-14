import { describe, expect, it } from "vitest";
import {
  categoryValues,
  crossFilterParameter,
  type Selectable,
} from "@/features/dashboards/crossFilter";
import type { ChartSpec, WidgetSpec } from "@/types/dashboard";

const PARAMETERS: Record<string, Selectable> = {
  period: { type: "time_range" },
  region: { type: "dimension", model: "sales", dimension: "region" },
  country: { type: "dimension", model: "sales", dimension: "country" },
};

function chart(overrides: Partial<ChartSpec> = {}): ChartSpec {
  return {
    type: "bar",
    x: { field: "country", kind: "category" },
    y: [{ field: "revenue", label: "Revenue" }],
    series: null,
    stack: false,
    orientation: "horizontal",
    colors: {},
    limits: { rows: 10, series: 8 },
    ...overrides,
  };
}

const BY_COUNTRY: WidgetSpec = {
  id: "by_country",
  type: "bar",
  query: { model: "sales", dimensions: ["country"] },
};

describe("crossFilterParameter", () => {
  it("lets bars set the parameter over their own model and dimension", () => {
    expect(crossFilterParameter(BY_COUNTRY, chart(), PARAMETERS)).toBe(
      "country",
    );
  });

  it("sets nothing from bars that no parameter filters on", () => {
    expect(
      crossFilterParameter(
        BY_COUNTRY,
        chart({ x: { field: "channel", kind: "category" } }),
        PARAMETERS,
      ),
    ).toBeNull();
  });

  it("sets nothing from bars over another model", () => {
    const other = { ...BY_COUNTRY, query: { model: "web" } };
    expect(crossFilterParameter(other, chart(), PARAMETERS)).toBeNull();
  });

  it("sets nothing from a line, a time axis or an SQL widget", () => {
    expect(
      crossFilterParameter(BY_COUNTRY, chart({ type: "line" }), PARAMETERS),
    ).toBeNull();
    expect(
      crossFilterParameter(
        BY_COUNTRY,
        chart({ x: { field: "country", kind: "time", grain: "month" } }),
        PARAMETERS,
      ),
    ).toBeNull();
    const sql = { id: "by_country", type: "bar" as const };
    expect(crossFilterParameter(sql, chart(), PARAMETERS)).toBeNull();
  });
});

describe("categoryValues", () => {
  it("keeps each category as its rows carry it, and skips a missing one", () => {
    const values = categoryValues(
      chart({ x: { field: "store", kind: "category" } }),
      [
        { name: "store", type: "INTEGER", role: "dimension" },
        { name: "revenue", type: "DOUBLE", role: "measure" },
      ],
      [
        [12, 10],
        [7, 4],
        [null, 1],
      ],
    );
    expect([...values]).toEqual([
      ["12", 12],
      ["7", 7],
    ]);
  });
});
