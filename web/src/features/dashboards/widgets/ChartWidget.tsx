import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Line,
  LineChart,
  Rectangle,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type BarShapeProps,
} from "recharts";
import { NEUTRAL, resolve, SERIES } from "@/components/charts/chartColors";
import {
  GRID_PROPS,
  Legend,
  TOOLTIP_PROPS,
  X_AXIS_PROPS,
  Y_AXIS_PROPS,
} from "@/components/charts/ChartFrame";
import { useIsDark } from "@/hooks/useIsDark";
import type { Cell, ChartSpec, RunWidget } from "@/types/dashboard";
import {
  chartData,
  formatPeriod,
  type ChartSeries,
  type Datum,
} from "../chartData";
import { categoryValues } from "../crossFilter";
import { formatValue, type ValueFormat } from "../format";
import { niceDomain, stepTicks } from "../scale";

/** The dimension parameter a click on a bar sets, and its value in this run. */
export interface ChartSelection {
  label: string;
  value: unknown;
  /** Sets the parameter, or clears it with null. */
  onSelect: (value: Cell) => void;
}

/** How strongly the bars that are not chosen still show. */
const UNCHOSEN_OPACITY = 0.35;

/** Bars with this many or fewer carry their value at the tip. */
const LABELLED_BARS = 12;
/** Roughly how wide a character of 11px tick text is. */
const CHAR_PX = 6.5;

function textWidth(text: string): number {
  return Math.ceil(text.length * CHAR_PX);
}

/** One measure's format when every series shares it, so the axis can say so. */
function sharedFormat(series: ChartSeries[]): ValueFormat {
  const [first] = series;
  if (!first) return {};
  const same = series.every(
    (s) =>
      s.measure.format === first.measure.format &&
      s.measure.unit === first.measure.unit &&
      s.measure.currency === first.measure.currency,
  );
  return same ? first.measure : {};
}

function ChartTooltip({
  datum,
  series,
  colors,
  title,
  hint,
}: {
  datum: Datum | undefined;
  series: ChartSeries[];
  colors: string[];
  title: string;
  /** What a click on this point does. */
  hint?: string;
}) {
  if (!datum) return null;
  return (
    <div
      className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-2.5 py-2 text-xs shadow-[var(--shadow-e2)]"
      style={{ fontSize: 12 }}
    >
      <p className="mb-1 text-text-secondary">{title}</p>
      {/* Every series at this x, so the pointer never has to find a line. */}
      <ul className="space-y-0.5">
        {series.map((s, i) => (
          <li key={s.key} className="flex items-center gap-2">
            <span
              aria-hidden
              className="h-0.5 w-3 shrink-0 rounded-full"
              style={{ backgroundColor: colors[i] }}
            />
            <span className="font-medium tabular-nums text-text-primary">
              {formatValue(datum[s.key] ?? null, s.measure)}
            </span>
            {series.length > 1 && (
              <span className="text-text-secondary">{s.label}</span>
            )}
          </li>
        ))}
      </ul>
      {hint && <p className="mt-1.5 text-2xs text-text-tertiary">{hint}</p>}
    </div>
  );
}

/** The value range a line draws: its lowest to its highest value, on round steps. */
function fitted(data: Datum[], series: ChartSeries[]) {
  let lo = Infinity;
  let hi = -Infinity;
  for (const d of data) {
    for (const s of series) {
      const v = d[s.key];
      if (typeof v === "number" && Number.isFinite(v)) {
        lo = Math.min(lo, v);
        hi = Math.max(hi, v);
      }
    }
  }
  return lo <= hi ? niceDomain(lo, hi) : null;
}

function describe(
  chart: ChartSpec,
  series: ChartSeries[],
  chosen: string | null,
  partial: string[],
): string {
  const measures = [...new Set(chart.y.map((m) => m.label))].join(" and ");
  const by =
    chart.x?.kind === "time" && chart.x.grain
      ? chart.x.grain
      : (chart.x?.field ?? "");
  const split =
    chart.series && series.length > 1
      ? `, one ${chart.type === "line" ? "line" : "bar"} per ${chart.series.field}: ${series.map((s) => s.label).join(", ")}`
      : "";
  const marked = chosen === null ? "" : ` ${chosen} is selected.`;
  const cut =
    partial.length === 0
      ? ""
      : ` The window holds only part of ${partial.join(" and ")}.`;
  return `A ${chart.type} chart of ${measures} by ${by}${split}.${marked}${cut}`;
}

/**
 * A line or bar widget, drawn from the chart spec the server sent with its
 * rows. The server decided the encoding (axes, series, stacking, orientation
 * and each series' colour slot); this only draws it.
 *
 * The chart is a picture with a text alternative. Every value it shows is
 * also in the widget's table view, so nothing depends on hovering. A period
 * the window holds only in part is drawn dashed, with a hollow point, as its
 * value is not comparable with the whole periods beside it.
 *
 * With a `selection`, a click on a bar filters the rest of the dashboard by
 * that bar's category, and a click on the chosen bar clears the filter. The
 * filter bar sets the same parameter, for anyone not using a pointer.
 */
export function ChartWidget({
  event,
  label,
  selection,
}: {
  event: RunWidget;
  label: string;
  selection?: ChartSelection;
}) {
  const dark = useIsDark();
  const chart = event.chart!;
  const rows = event.rows!;
  const { data, series, partial } = useMemo(
    () => chartData(chart, rows.columns, rows.data),
    [chart, rows],
  );
  const cells = useMemo(
    () => categoryValues(chart, rows.columns, rows.data),
    [chart, rows],
  );
  const chosen =
    selection && selection.value != null ? String(selection.value) : null;
  const pick = (d: Datum | undefined) => {
    // A row without a category names nothing a filter could hold.
    if (!selection || !d || d.x === "") return;
    selection.onSelect(d.x === chosen ? null : (cells.get(d.x) ?? d.x));
  };
  const hint = (d: Datum | undefined) =>
    !selection || !d || d.x === ""
      ? undefined
      : d.x === chosen
        ? `Click to stop filtering by ${selection.label}`
        : `Click to filter by ${selection.label}`;
  const colors = series.map((s) =>
    resolve(s.slot === null ? NEUTRAL : SERIES[s.slot % SERIES.length], dark),
  );
  const format = sharedFormat(series);
  const values = data.flatMap((d) =>
    series.map((s) =>
      typeof d[s.key] === "number" ? (d[s.key] as number) : 0,
    ),
  );
  const extreme = values.reduce(
    (a, b) => (Math.abs(b) > Math.abs(a) ? b : a),
    0,
  );
  // One style per axis: compact throughout once it reaches 10,000.
  const compact = Math.abs(extreme) >= 10_000 ? ("always" as const) : false;
  const tick = (value: unknown) =>
    formatValue(typeof value === "number" ? value : null, format, {
      compact,
      trim: true,
    });
  // A line's value axis fits its data: lap times near 80 seconds drawn from
  // zero would be flat lines. A bar's length is its value, so bars keep zero.
  const fit = chart.type === "line" ? fitted(data, series) : null;
  const valueTicks = fit ? stepTicks(fit.domain, fit.step) : [extreme];
  const valueWidth = Math.min(
    80,
    Math.max(32, ...valueTicks.map((v) => textWidth(tick(v)) + 8)),
  );
  const grain = chart.x?.kind === "time" ? (chart.x.grain ?? null) : null;
  const numeric = chart.x?.kind === "number";
  // A lap or a year names a thing, and 2,024 would read as an amount.
  const whole = /INT/i.test(
    rows.columns.find((c) => c.name === chart.x?.field)?.type ?? "",
  );
  const numberTick = (value: unknown) =>
    formatValue(
      typeof value === "number" ? value : null,
      {},
      { grouping: !whole, trim: true },
    );
  const xLabel = (x: string) =>
    chart.x?.kind === "time"
      ? formatPeriod(x, grain, true)
      : numeric
        ? formatValue(x, {}, { grouping: !whole })
        : x || "(none)";
  const cut = data.filter((d) => partial.has(d.x)).map((d) => xLabel(d.x));
  const legend =
    series.length > 1 ? (
      <Legend
        mark={chart.type === "line" ? "line" : "square"}
        items={series.map((s, i) => ({ label: s.label, color: colors[i] }))}
      />
    ) : null;
  const tooltipTitle = (x: string) =>
    partial.has(x) ? `${xLabel(x)} (partial)` : xLabel(x);
  const tooltip = (
    <Tooltip
      {...TOOLTIP_PROPS}
      cursor={
        chart.type === "line"
          ? { stroke: "var(--border-strong)", strokeWidth: 1 }
          : TOOLTIP_PROPS.cursor
      }
      content={({ active, payload }) =>
        active ? (
          <ChartTooltip
            datum={payload?.[0]?.payload as Datum | undefined}
            series={series}
            colors={colors}
            title={tooltipTitle(
              String((payload?.[0]?.payload as Datum)?.x ?? ""),
            )}
            hint={hint(payload?.[0]?.payload as Datum | undefined)}
          />
        ) : null
      }
    />
  );

  let plot;
  if (chart.type === "line") {
    // One point has no line to draw, so it shows as a dot.
    const dots = data.length === 1;
    plot = (
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid {...GRID_PROPS} />
        {/* A number axis draws each point at its value, so a lap missing
            from the data leaves a gap of its own width. */}
        <XAxis
          {...X_AXIS_PROPS}
          {...(numeric
            ? {
                dataKey: "xn",
                type: "number" as const,
                domain: ["dataMin", "dataMax"],
                allowDecimals: !whole,
                tickFormatter: numberTick,
              }
            : {
                dataKey: "x",
                tickFormatter: (x: string) => formatPeriod(x, grain),
              })}
          minTickGap={24}
        />
        <YAxis
          {...Y_AXIS_PROPS}
          allowDecimals
          width={valueWidth}
          tickFormatter={tick}
          {...(fit && { domain: fit.domain, ticks: valueTicks, interval: 0 })}
        />
        {tooltip}
        {/* A period the window cuts is a dashed piece under the solid line,
            joined to the whole period beside it. */}
        {partial.size > 0 &&
          series.map((s, i) => (
            <Line
              key={`${s.key}Partial`}
              dataKey={`${s.key}Partial`}
              name={s.label}
              type="linear"
              stroke={colors[i]}
              strokeWidth={2}
              strokeDasharray="4 4"
              strokeLinecap="round"
              dot={(props: {
                cx?: number;
                cy?: number;
                index: number;
                payload: Datum;
              }) =>
                partial.has(props.payload.x) &&
                props.cx != null &&
                props.cy != null ? (
                  <circle
                    key={props.index}
                    cx={props.cx}
                    cy={props.cy}
                    r={4}
                    fill="var(--bg-surface)"
                    stroke={colors[i]}
                    strokeWidth={2}
                  />
                ) : (
                  <g key={props.index} />
                )
              }
              activeDot={{
                r: 4,
                fill: colors[i],
                stroke: "var(--bg-surface)",
                strokeWidth: 2,
              }}
              isAnimationActive={false}
            />
          ))}
        {series.map((s, i) => (
          <Line
            key={s.key}
            dataKey={partial.size > 0 ? `${s.key}Whole` : s.key}
            name={s.label}
            type="linear"
            stroke={colors[i]}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
            dot={
              dots
                ? {
                    r: 4,
                    fill: colors[i],
                    stroke: "var(--bg-surface)",
                    strokeWidth: 2,
                  }
                : false
            }
            activeDot={{
              r: 4,
              fill: colors[i],
              stroke: "var(--bg-surface)",
              strokeWidth: 2,
            }}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    );
  } else {
    const horizontal = chart.orientation === "horizontal";
    // A single series of a few bars carries each value at its tip, so the value
    // axis has nothing left to say and is dropped.
    const labelled = series.length === 1 && data.length <= LABELLED_BARS;
    const longest = data.reduce(
      (n, d) => Math.max(n, (d.x || "(none)").length),
      0,
    );
    const categoryWidth = Math.min(
      120,
      Math.max(32, Math.min(longest, 18) * CHAR_PX + 8),
    );
    const category = (x: string) => {
      const text = x || "(none)";
      return text.length > 18 ? `${text.slice(0, 17)}…` : text;
    };
    const last = series.length - 1;
    plot = (
      <BarChart
        data={data}
        layout={horizontal ? "vertical" : "horizontal"}
        barGap={2}
        margin={{
          top: labelled && !horizontal ? 18 : 8,
          right: labelled && horizontal ? valueWidth : 12,
          bottom: 0,
          left: 0,
        }}
      >
        {/* Gridlines read against a value axis; labelled bars have none. */}
        {!labelled && (
          <CartesianGrid
            {...GRID_PROPS}
            vertical={horizontal}
            horizontal={!horizontal}
          />
        )}
        {horizontal ? (
          <>
            <XAxis
              type="number"
              {...X_AXIS_PROPS}
              tickFormatter={tick}
              hide={labelled}
            />
            <YAxis
              type="category"
              dataKey="x"
              {...Y_AXIS_PROPS}
              allowDecimals
              width={categoryWidth}
              tickFormatter={category}
              interval={0}
            />
          </>
        ) : (
          <>
            <XAxis
              dataKey="x"
              {...X_AXIS_PROPS}
              tickFormatter={category}
              minTickGap={8}
            />
            <YAxis
              {...Y_AXIS_PROPS}
              allowDecimals
              width={valueWidth}
              tickFormatter={tick}
              hide={labelled}
            />
          </>
        )}
        {tooltip}
        {series.map((s, i) => {
          // The data end is rounded and the baseline square; in a stack only the
          // top segment has a data end.
          const end = !chart.stack || i === last;
          const radius = end
            ? horizontal
              ? ([0, 4, 4, 0] as [number, number, number, number])
              : ([4, 4, 0, 0] as [number, number, number, number])
            : 0;
          return (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.label}
              fill={colors[i]}
              maxBarSize={24}
              radius={radius}
              stackId={chart.stack ? "stack" : undefined}
              // A 2px gap in the surface colour between stacked segments.
              stroke={chart.stack ? "var(--bg-surface)" : undefined}
              strokeWidth={chart.stack ? 2 : 0}
              isAnimationActive={false}
              {...(selection
                ? {
                    // The whole band answers a click, not only the thin bar.
                    background: { fill: "transparent" },
                    className: "cursor-pointer",
                    onClick: (_: unknown, index: number) => pick(data[index]),
                  }
                : {})}
              // The chosen bar keeps its colour; the others recede.
              shape={
                chosen === null
                  ? undefined
                  : (props: BarShapeProps) => (
                      <Rectangle
                        {...props}
                        fillOpacity={
                          (props.payload as Datum).x === chosen
                            ? 1
                            : UNCHOSEN_OPACITY
                        }
                      />
                    )
              }
            >
              {labelled && (
                <LabelList
                  dataKey={s.key}
                  position={horizontal ? "right" : "top"}
                  formatter={(v) => tick(v)}
                  // A click on the value lands on the bar's band beneath it.
                  style={{
                    fontSize: 11,
                    fill: "var(--text-secondary)",
                    pointerEvents: "none",
                  }}
                />
              )}
            </Bar>
          );
        })}
      </BarChart>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {legend && <div className="flex justify-end">{legend}</div>}
      {/* The plot is a picture; the label and description are its text
          alternative, and the table view lists every value. */}
      <div
        className="h-56 [&_*:focus:not(:focus-visible)]:outline-none"
        role="img"
        aria-label={`${label}. ${describe(chart, series, chosen, cut)}`}
        data-testid="widget-chart"
      >
        <ResponsiveContainer width="100%" height="100%">
          {plot}
        </ResponsiveContainer>
      </div>
    </div>
  );
}
