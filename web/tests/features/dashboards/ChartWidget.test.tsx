import { cloneElement, type ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@tests/utils";
import { ChartWidget } from "@/features/dashboards/widgets/ChartWidget";
import type { Cell, RunWidget } from "@/types/dashboard";

// jsdom lays nothing out, so a responsive chart never learns its size and
// draws no axis. Give it one.
vi.mock("recharts", async (importOriginal) => {
  const recharts = await importOriginal<typeof import("recharts")>();
  return {
    ...recharts,
    ResponsiveContainer: ({ children }: { children: ReactElement }) =>
      cloneElement(children as ReactElement<Record<string, unknown>>, {
        width: 400,
        height: 224,
      }),
  };
});

/** An SQL line over a number axis: a lap, or a year. */
function numbered(type: string, data: Cell[][]): RunWidget {
  return {
    type: "widget",
    id: "laps",
    state: "ok",
    scope: { window: null, compare_window: null, applied: [], skipped: [] },
    rows: {
      columns: [
        { name: "lap", type, role: "dimension" },
        { name: "seconds", type: "DOUBLE", role: "measure" },
      ],
      data,
      truncated: false,
      query_id: "",
    },
    chart: {
      type: "line",
      x: { field: "lap", kind: "number" },
      y: [{ field: "seconds", label: "seconds" }],
      series: null,
      stack: false,
      orientation: null,
      colors: {},
      limits: { rows: 5000, series: 8 },
    },
  };
}

/** Two drivers over laps, one pit stop each, each named at the end of its line. */
function racing(data: Cell[][]): RunWidget {
  const event = numbered("BIGINT", data);
  event.rows!.columns = [
    { name: "lap", type: "BIGINT", role: "dimension" },
    { name: "driver", type: "VARCHAR", role: "dimension" },
    { name: "seconds", type: "DOUBLE", role: "measure" },
    { name: "pit", type: "BOOLEAN", role: null },
    { name: "stop", type: "DOUBLE", role: null },
    { name: "acronym", type: "VARCHAR", role: null },
  ];
  event.chart = {
    ...event.chart!,
    series: { field: "driver", keys: ["PER", "VER"] },
    colors: { PER: 0 },
    styles: { VER: { color: "#3671c6" } },
    markers: [
      { type: "point", when: "pit", label: "Pit stop", size: "stop" },
      { type: "end", label: "acronym" },
    ],
  };
  return event;
}

function ticks(container: HTMLElement, side: "bottom" | "left"): string[] {
  return [
    // Recharts draws tick labels in a layer of their own, outside the axis.
    ...container.querySelectorAll(
      `text.recharts-cartesian-axis-tick-value[orientation="${side}"]`,
    ),
  ].map((tick) => tick.textContent ?? "");
}

const xTicks = (container: HTMLElement) => ticks(container, "bottom");

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
  it("draws a number axis to scale, smallest first", () => {
    // Wide integers arrive as text, which would put lap 10 before lap 9.
    const { container } = render(
      <ChartWidget
        event={numbered("BIGINT", [
          ["9", 81.2],
          ["10", 80.9],
          ["2", 82.4],
        ])}
        label="Lap times"
      />,
    );

    // A tick a lap, smallest first, with lap 10 after lap 9.
    expect(xTicks(container)).toEqual([
      "2",
      "3",
      "4",
      "5",
      "6",
      "7",
      "8",
      "9",
      "10",
    ]);
  });

  it("fits a line's value axis to its data rather than to zero", () => {
    const { container } = render(
      <ChartWidget
        event={numbered("BIGINT", [
          ["1", 81.2],
          ["2", 99.7],
        ])}
        label="Lap times"
      />,
    );

    expect(ticks(container, "left")).toEqual(["80", "85", "90", "95", "100"]);
  });

  it("draws a series in the colour its rows gave, in either theme", () => {
    const event = numbered("BIGINT", [
      ["1", "VER", 80.1],
      ["1", "PER", 80.9],
    ]);
    event.rows!.columns.splice(1, 0, {
      name: "driver",
      type: "VARCHAR",
      role: "dimension",
    });
    event.rows!.data = [
      ["1", "VER", 80.1],
      ["1", "PER", 80.9],
      ["2", "VER", 79.8],
      ["2", "PER", 80.4],
    ];
    event.chart = {
      ...event.chart!,
      series: { field: "driver", keys: ["PER", "VER"] },
      colors: { PER: 0 },
      styles: { VER: { color: "#3671c6" } },
    };
    const { container } = render(
      <ChartWidget event={event} label="Lap times" />,
    );

    const strokes = [
      ...container.querySelectorAll("path.recharts-line-curve"),
    ].map((path) => path.getAttribute("stroke"));
    expect(strokes).toEqual(["#2a78d6", "#3671c6"]);
  });

  it("marks a line only where the marker column is true", () => {
    const event = racing([
      ["1", "VER", 80.1, false, null, "VER"],
      ["2", "VER", 99.3, true, 2.4, "VER"],
      ["3", "VER", 80.4, false, null, "VER"],
      ["1", "PER", 80.9, false, null, "PER"],
      ["2", "PER", 80.6, false, null, "PER"],
      ["3", "PER", 98.8, true, 3.1, "PER"],
    ]);
    const { container } = render(
      <ChartWidget event={event} label="Lap times" />,
    );

    // One mark each, ink ringed in its line's colour, the slower stop larger.
    const marks = [...container.querySelectorAll("circle")].map((c) => [
      c.getAttribute("stroke"),
      Number(c.getAttribute("r")),
    ]);
    expect(marks).toEqual([
      ["#2a78d6", 6],
      ["#3671c6", 3],
    ]);
  });

  it("names each line at its end in place of a legend", () => {
    const event = racing([
      ["1", "VER", 80.1, false, null, "VER"],
      ["2", "VER", 80.0, false, null, "VER"],
      ["1", "PER", 80.9, false, null, "PER"],
      ["2", "PER", 80.6, false, null, "PER"],
    ]);
    render(<ChartWidget event={event} label="Lap times" />);

    const plot = screen.getByTestId("widget-chart");
    expect(plot.querySelectorAll("text")).not.toHaveLength(0);
    expect(
      [...plot.querySelectorAll("g[aria-hidden] text")].map(
        (t) => t.textContent,
      ),
    ).toEqual(["PER", "VER"]);
    // The names say what the legend would.
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("reads an integer axis without thousands separators", () => {
    const { container } = render(
      <ChartWidget
        event={numbered("INTEGER", [
          [2023, 5],
          [2024, 7],
          [2025, 6],
        ])}
        label="Orders by year"
      />,
    );

    expect(xTicks(container)).toContain("2024");
    expect(xTicks(container).join(" ")).not.toMatch(/2,0/);
  });

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
