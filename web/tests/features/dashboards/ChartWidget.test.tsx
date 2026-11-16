import { describe, expect, it } from "vitest";
import { render, screen } from "@tests/utils";
import { ChartWidget } from "@/features/dashboards/widgets/ChartWidget";
import type { RunWidget } from "@/types/dashboard";

function weekly(partial: string[]): RunWidget {
  return {
    type: "widget",
    id: "trend",
    state: "ok",
    scope: { window: null, compare_window: null, applied: [], skipped: [] },
    rows: {
      columns: [
        { name: "week", type: "DATE", role: "time" },
        { name: "revenue", type: "DECIMAL(38,2)", role: "measure" },
      ],
      data: [
        ["2026-08-17", "50"],
        ["2026-08-24", "100"],
        ["2026-11-09", "40"],
      ],
      truncated: false,
      query_id: "",
    },
    chart: {
      type: "line",
      x: { field: "week", kind: "time", grain: "week", partial },
      y: [{ field: "revenue", label: "Revenue" }],
      series: null,
      stack: false,
      orientation: null,
      colors: {},
      limits: { rows: null, series: 8 },
    },
  };
}

describe("ChartWidget", () => {
  it("says which periods the window holds only in part", () => {
    render(
      <ChartWidget
        event={weekly(["2026-08-17", "2026-11-09"])}
        label="Revenue by week"
      />,
    );

    expect(
      screen.getByRole("img", {
        name: "Revenue by week. A line chart of Revenue by week. The window holds only part of Week of 17 Aug 2026 and Week of 9 Nov 2026.",
      }),
    ).toBeInTheDocument();
  });

  it("says nothing of periods when the window holds them all", () => {
    render(<ChartWidget event={weekly([])} label="Revenue by week" />);

    expect(
      screen.getByRole("img", {
        name: "Revenue by week. A line chart of Revenue by week.",
      }),
    ).toBeInTheDocument();
  });
});
