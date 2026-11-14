import { describe, expect, it } from "vitest";
import { parseDashboardSearch, runParams } from "@/features/dashboards/params";

describe("dashboard search", () => {
  it("keeps only the parameters, as plain values", () => {
    const search = parseDashboardSearch({
      "p.period": "last_7_days",
      "p.country": null,
      "p.store": 12,
      "p.": "nameless",
      "p.nested": { a: 1 },
      tab: "x",
    });
    expect(search).toEqual({
      "p.period": "last_7_days",
      "p.country": null,
      "p.store": 12,
    });
    expect(runParams(search)).toEqual({
      period: "last_7_days",
      country: null,
      store: 12,
    });
  });
});
