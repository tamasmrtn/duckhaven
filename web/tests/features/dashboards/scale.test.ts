import { describe, expect, it } from "vitest";
import { niceDomain, niceStep, stepTicks } from "@/features/dashboards/scale";

describe("niceStep", () => {
  it("picks 1, 2, 2.5 or 5 times a power of ten", () => {
    expect(niceStep(100)).toBe(25);
    expect(niceStep(7)).toBe(2);
    expect(niceStep(0.9)).toBe(0.25);
    expect(niceStep(3600)).toBe(1000);
  });

  it("falls back to 1 for a span with nothing in it", () => {
    expect(niceStep(0)).toBe(1);
    expect(niceStep(Number.NaN)).toBe(1);
  });
});

describe("niceDomain", () => {
  it("fits the values, not zero", () => {
    // Lap times sit far from zero; a zero-based axis flattens every line.
    expect(niceDomain(81.2, 99.7)).toEqual({ domain: [80, 100], step: 5 });
  });

  it("reaches zero when the values come close to it", () => {
    expect(niceDomain(5, 95).domain).toEqual([0, 100]);
  });

  it("spans negative values", () => {
    expect(niceDomain(-3.2, 4.1)).toEqual({ domain: [-4, 6], step: 2 });
  });

  it("gives a flat line room either side", () => {
    expect(niceDomain(100, 100).domain).toEqual([90, 110]);
    // A count that never goes negative gets no negative range.
    expect(niceDomain(0, 0).domain).toEqual([0, 1]);
  });
});

describe("stepTicks", () => {
  it("puts a tick at every step", () => {
    expect(stepTicks([80, 100], 5)).toEqual([80, 85, 90, 95, 100]);
  });

  it("leaves no float noise in a tick", () => {
    expect(stepTicks([0.1, 0.3], 0.1)).toEqual([0.1, 0.2, 0.3]);
  });
});
