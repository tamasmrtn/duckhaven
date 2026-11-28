import { describe, expect, it } from "vitest";
import { placeLabels } from "@/features/dashboards/widgets/endLabels";

describe("placeLabels", () => {
  it("leaves labels that do not collide where their lines end", () => {
    expect(placeLabels([10, 50, 30], 12, [0, 100])).toEqual([10, 50, 30]);
  });

  it("spreads colliding labels evenly around where they want to be", () => {
    // Two lines ending at one value: one label above, one below.
    expect(placeLabels([50, 50], 12, [0, 100])).toEqual([44, 56]);
    expect(placeLabels([40, 42, 44], 10, [0, 100])).toEqual([32, 42, 52]);
  });

  it("keeps each label beside its own line, in order", () => {
    const placed = placeLabels([60, 20, 58, 21], 12, [0, 100])!;
    expect(placed[1]).toBeLessThan(placed[3]);
    expect(placed[3]).toBeLessThan(placed[2]);
    expect(placed[2]).toBeLessThan(placed[0]);
    const sorted = [...placed].sort((a, b) => a - b);
    sorted.slice(1).forEach((y, i) => {
      expect(y - sorted[i]).toBeGreaterThanOrEqual(12 - 1e-9);
    });
  });

  it("pushes labels inside the plot", () => {
    expect(placeLabels([0, 1], 12, [5, 100])).toEqual([5, 17]);
    expect(placeLabels([99, 100], 12, [0, 100])).toEqual([88, 100]);
  });

  it("says when the labels cannot fit", () => {
    expect(placeLabels([1, 2, 3], 60, [0, 100])).toBeNull();
    expect(placeLabels([], 12, [0, 100])).toEqual([]);
  });
});
