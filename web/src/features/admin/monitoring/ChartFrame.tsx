import type { ReactNode } from "react";
import {
  formatAbsoluteTimestamp,
  formatClockTick,
  windowTicks,
} from "../metricsTime";

/**
 * Shared chrome for every chart on the monitoring page.
 *
 * The charts are read as a stack — a spike in one is only meaningful next to the
 * others at the same instant — so title placement, height, axis treatment and the
 * time scale all live here rather than being re-specified per chart and drifting.
 */
export function ChartFrame({
  title,
  subtitle,
  legend,
  testId,
  height = "h-48",
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  legend?: ReactNode;
  testId: string;
  height?: string;
  children: ReactNode;
}) {
  return (
    <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
            {title}
          </h3>
          {subtitle && (
            <p className="mt-0.5 text-2xs text-text-tertiary">{subtitle}</p>
          )}
        </div>
        {legend}
      </div>
      <div className={height} data-testid={testId}>
        {children}
      </div>
    </section>
  );
}

/**
 * The identity channel. Always rendered for two or more series so nothing depends
 * on matching colours by eye, and it carries each series' window total — which
 * doubles as the visible-value relief the lighter categorical hues need to be
 * legible on a light surface.
 */
export function Legend({
  items,
}: {
  items: { label: string; color: string; value?: string }[];
}) {
  return (
    <ul className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="size-2 shrink-0 rounded-[2px]"
            style={{ backgroundColor: item.color }}
          />
          {/* Text stays in ink tokens — a light series hue is illegible as text. */}
          <span className="text-2xs text-text-secondary">{item.label}</span>
          {item.value !== undefined && (
            <span className="font-mono text-2xs font-tabular text-text-primary">
              {item.value}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Shared x-axis config: a real time scale with round clock ticks. */
export function timeAxisProps(startMs: number, endMs: number) {
  return {
    dataKey: "t",
    type: "number" as const,
    scale: "time" as const,
    domain: [startMs, endMs],
    ticks: windowTicks(startMs, endMs),
    tickFormatter: (value: number) => formatClockTick(Number(value)),
    tick: { fontSize: 11, fill: "var(--text-tertiary)" },
    tickLine: false,
    axisLine: { stroke: "var(--border-subtle)" },
  };
}

export const Y_AXIS_PROPS = {
  tick: { fontSize: 11, fill: "var(--text-tertiary)" },
  tickLine: false,
  axisLine: false,
  width: 36,
  allowDecimals: false,
};

/** Hairline, solid, one step off the surface — recessive by design. */
export const GRID_PROPS = {
  stroke: "var(--border-subtle)",
  strokeDasharray: "0",
  vertical: false,
};

export const TOOLTIP_PROPS = {
  // Recharts types the label as ReactNode; every chart here plots a numeric time
  // scale, so the value really is epoch milliseconds.
  labelFormatter: (label: ReactNode) => formatAbsoluteTimestamp(Number(label)),
  contentStyle: {
    background: "var(--bg-elevated)",
    border: "1px solid var(--border-subtle)",
    borderRadius: "var(--radius-md)",
    fontSize: 12,
    boxShadow: "var(--shadow-e2)",
  },
  labelStyle: { color: "var(--text-secondary)", marginBottom: 4 },
  itemStyle: { color: "var(--text-primary)" },
  cursor: { fill: "var(--accent)", fillOpacity: 0.4 },
};
