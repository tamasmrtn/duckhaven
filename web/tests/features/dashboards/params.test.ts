import { describe, expect, it } from "vitest";
import {
  hasParameters,
  parseDashboardSearch,
  runParams,
  withoutParameters,
  withParameter,
} from "@/features/dashboards/params";

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

  it("keeps present mode and a refresh it offers, apart from the parameters", () => {
    const search = parseDashboardSearch({
      "p.country": "NL",
      present: true,
      refresh: 15,
    });
    expect(search).toEqual({ "p.country": "NL", present: true, refresh: 15 });
    expect(runParams(search)).toEqual({ country: "NL" });
    expect(withoutParameters(search)).toEqual({ present: true, refresh: 15 });
    expect(hasParameters(withoutParameters(search))).toBe(false);

    // Only what the app writes: no other interval, and present only as true.
    expect(parseDashboardSearch({ present: "yes", refresh: 1 })).toEqual({});
  });

  it("sets a parameter, or takes it out of the URL", () => {
    const search = { "p.country": "NL", present: true };
    expect(withParameter(search, "country", "DE")).toEqual({
      "p.country": "DE",
      present: true,
    });
    expect(withParameter(search, "country", undefined)).toEqual({
      present: true,
    });
    expect(withParameter(search, "country", null)).toEqual({
      "p.country": null,
      present: true,
    });
  });
});
