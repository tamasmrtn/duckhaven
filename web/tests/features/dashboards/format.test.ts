import { describe, expect, it } from "vitest";
import {
  formatChange,
  formatTime,
  formatValue,
  toNumber,
} from "@/features/dashboards/format";

const USD = { format: "currency" as const, currency: "USD" };

describe("formatValue", () => {
  it("reads a DECIMAL that arrived as a string as the number it is", () => {
    expect(toNumber("48210.50")).toBe(48210.5);
    expect(formatValue("48210.50", USD)).toBe("$48,210.50");
  });

  it("compacts a glanceable figure only once it is long", () => {
    expect(formatValue("48210.50", USD, { compact: true })).toBe("$48.2K");
    expect(formatValue(1284, { unit: "orders" }, { compact: true })).toBe(
      "1,284 orders",
    );
    expect(formatValue(12890, {}, { compact: true })).toBe("12.9K");
  });

  it("reads every tick on an axis in one style", () => {
    // An axis that reaches 10,000 compacts its small ticks too, and drops
    // the zero cents a tick has no use for.
    const tick = { compact: "always" as const, trim: true };
    expect(formatValue(8000, USD, tick)).toBe("$8K");
    expect(formatValue(0, USD, tick)).toBe("$0");
    expect(formatValue(0, USD, { trim: true })).toBe("$0");
    expect(formatValue(2.5, USD, { trim: true })).toBe("$2.5");
  });

  it("reads a percent as a ratio, and a change in one in points", () => {
    expect(formatValue(0.031, { format: "percent" })).toBe("3.1%");
    expect(formatValue(0.004, { format: "percent" }, { signed: true })).toBe(
      "+0.4 pts",
    );
  });

  it("signs a change, and never a zero", () => {
    expect(formatValue("5350.5", USD, { signed: true })).toBe("+$5,350.50");
    expect(formatValue(-12, {}, { signed: true })).toBe("-12");
    expect(formatValue(0, {}, { signed: true })).toBe("0");
    expect(formatChange(0.124837)).toBe("+12.5%");
  });

  it("reads durations in seconds and bytes in their largest unit", () => {
    expect(formatValue(0.45, { format: "duration" })).toBe("450 ms");
    expect(formatValue(3.25, { format: "duration" })).toBe("3.3 s");
    expect(formatValue(125, { format: "duration" })).toBe("2m 5s");
    expect(formatValue(7380, { format: "duration" })).toBe("2h 3m");
    expect(formatValue(2554, { format: "bytes" })).toBe("2.5 KB");
  });

  it("shows what is not a number as it came, and nothing as a dash", () => {
    expect(formatValue("NL")).toBe("NL");
    expect(formatValue(null, USD)).toBe("—");
  });
});

describe("formatTime", () => {
  it("reads a period's midnight as its date and keeps any other time", () => {
    expect(formatTime("2026-05-18T00:00:00")).toBe("2026-05-18");
    expect(formatTime("2026-05-18 00:00:00+00")).toBe("2026-05-18");
    expect(formatTime("2026-05-18T09:30:00")).toBe("2026-05-18T09:30:00");
    expect(formatTime("2026-05-18")).toBe("2026-05-18");
    expect(formatTime(null)).toBe("—");
  });
});
