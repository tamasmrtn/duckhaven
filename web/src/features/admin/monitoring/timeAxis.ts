import type { ReactNode } from "react";
import { TOOLTIP_PROPS, X_AXIS_PROPS } from "@/components/charts/ChartFrame";
import {
  formatAbsoluteTimestamp,
  formatWindowTick,
  windowTicks,
} from "../metricsTime";

/**
 * x-axis config: a real time scale with round clock ticks.
 *
 * The monitoring charts are read as a stack — a spike in one is only meaningful
 * next to the others at the same instant — so they share this one time scale
 * rather than setting it per chart.
 */
export function timeAxisProps(startMs: number, endMs: number) {
  return {
    ...X_AXIS_PROPS,
    dataKey: "x",
    type: "number" as const,
    scale: "time" as const,
    domain: [startMs, endMs],
    ticks: windowTicks(startMs, endMs),
    tickFormatter: (value: number) =>
      formatWindowTick(Number(value), endMs - startMs),
  };
}

export const TIME_TOOLTIP_PROPS = {
  ...TOOLTIP_PROPS,
  // Recharts types the label as ReactNode; every chart here plots a numeric time
  // scale, so the value really is epoch milliseconds.
  labelFormatter: (label: ReactNode) => formatAbsoluteTimestamp(Number(label)),
};
