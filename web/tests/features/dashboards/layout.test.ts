import { describe, expect, it } from "vitest";
import { rowLayout } from "@/features/dashboards/layout";
import type { WidgetSpec } from "@/types/dashboard";

const w = (width?: number): WidgetSpec => ({ id: "w", type: "kpi", width });

describe("rowLayout", () => {
  it("shares a row equally among widgets without a width", () => {
    expect(rowLayout([w(), w(), w()])).toEqual({ widths: [4, 4, 4], rest: 0 });
  });

  it("gives widgets without a width what the others leave", () => {
    expect(rowLayout([w(8), w()])).toEqual({ widths: [8, 4], rest: 0 });
    expect(rowLayout([w(6), w(), w()])).toEqual({ widths: [6, 3, 3], rest: 0 });
  });

  it("leaves the rest of a row empty rather than stretching fixed widths", () => {
    expect(rowLayout([w(4), w(4)])).toEqual({ widths: [4, 4], rest: 4 });
  });
});
