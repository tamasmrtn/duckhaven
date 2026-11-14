import { useId, type ReactNode } from "react";
import { SectionLabel } from "@/components/ui/section-label";

/**
 * Shared chrome for every chart in the app.
 *
 * Charts are read side by side, so title placement, height and axis treatment
 * live here rather than being re-specified per chart and drifting. What the
 * x-axis measures belongs to the caller: a time scale is the monitoring page's
 * (`timeAxisProps` there), not something every chart has.
 */
export function ChartFrame({
  title,
  subtitle,
  legend,
  testId,
  height = "h-48",
  emptyMessage,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  legend?: ReactNode;
  testId: string;
  height?: string;
  // When set, the chart has nothing to draw: say so instead of plotting an
  // arbitrary 0-4 axis and a legend of zeros, which reads as measured zeros.
  emptyMessage?: string;
  children: ReactNode;
}) {
  const subtitleId = useId();
  return (
    <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div>
          <SectionLabel>{title}</SectionLabel>
          {subtitle && (
            <p id={subtitleId} className="mt-0.5 text-2xs text-text-tertiary">
              {subtitle}
            </p>
          )}
        </div>
        {emptyMessage ? null : legend}
      </div>
      {/* A clicked chart takes focus; show the ring for keyboard focus only. */}
      {/* The chart is a picture; the title and subtitle are its text
          alternative, and the same data is listed in the table below. */}
      <div
        className={`${height} [&_*:focus:not(:focus-visible)]:outline-none`}
        data-testid={testId}
        role="img"
        aria-label={title}
        aria-describedby={subtitle ? subtitleId : undefined}
      >
        {emptyMessage ? (
          <div className="flex h-full items-center justify-center text-sm text-text-tertiary">
            {emptyMessage}
          </div>
        ) : (
          children
        )}
      </div>
    </section>
  );
}

/**
 * The identity channel. Always rendered for two or more series so nothing depends
 * on matching colours by eye, and it can carry each series' value, so the figures
 * are readable without hovering.
 */
export function Legend({
  items,
  mark = "square",
}: {
  items: { label: string; color: string; value?: string }[];
  // The key mirrors the mark: a square for bars and areas, a stroke for lines.
  mark?: "square" | "line";
}) {
  return (
    <ul className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className={
              mark === "line"
                ? "h-0.5 w-3 shrink-0 rounded-full"
                : "size-2 shrink-0 rounded-[2px]"
            }
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

/** Tick text and baseline for an x-axis; the caller adds what it measures. */
export const X_AXIS_PROPS = {
  tick: { fontSize: 11, fill: "var(--text-tertiary)" },
  tickLine: false,
  axisLine: { stroke: "var(--border-subtle)" },
};

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

/** Tooltip styling; the caller adds a `labelFormatter` for its x values. */
export const TOOLTIP_PROPS = {
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
