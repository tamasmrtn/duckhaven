import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { resolve, SERIES } from "@/components/charts/chartColors";
import {
  GRID_PROPS,
  Legend,
  TOOLTIP_PROPS,
  X_AXIS_PROPS,
  Y_AXIS_PROPS,
} from "@/components/charts/ChartFrame";
import { useIsDark } from "@/hooks/useIsDark";
import type { ChartSpec, RunWidget } from "@/types/dashboard";
import {
  chartData,
  formatPeriod,
  type ChartSeries,
  type Datum,
} from "../chartData";
import { formatValue, type ValueFormat } from "../format";

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
}: {
  datum: Datum | undefined;
  series: ChartSeries[];
  colors: string[];
  title: string;
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
    </div>
  );
}

function describe(chart: ChartSpec, series: ChartSeries[]): string {
  const measures = [...new Set(chart.y.map((m) => m.label))].join(" and ");
  const by =
    chart.x?.kind === "time" && chart.x.grain
      ? chart.x.grain
      : (chart.x?.field ?? "");
  const split =
    chart.series && series.length > 1
      ? `, one ${chart.type === "line" ? "line" : "bar"} per ${chart.series.field}: ${series.map((s) => s.label).join(", ")}`
      : "";
  return `A ${chart.type} chart of ${measures} by ${by}${split}.`;
}

/**
 * A line or bar widget, drawn from the chart spec the server sent with its
 * rows. The server decided the encoding (axes, series, stacking, orientation
 * and each series' colour slot); this only draws it.
 *
 * The chart is a picture with a text alternative. Every value it shows is
 * also in the widget's table view, so nothing depends on hovering.
 */
export function ChartWidget({
  event,
  label,
}: {
  event: RunWidget;
  label: string;
}) {
  const dark = useIsDark();
  const chart = event.chart!;
  const rows = event.rows!;
  const { data, series } = useMemo(
    () => chartData(chart, rows.columns, rows.data),
    [chart, rows],
  );
  const colors = series.map((s) =>
    resolve(SERIES[s.slot % SERIES.length], dark),
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
  const valueWidth = Math.min(80, Math.max(32, textWidth(tick(extreme)) + 8));
  const grain = chart.x?.kind === "time" ? (chart.x.grain ?? null) : null;
  const xLabel = (x: string) =>
    chart.x?.kind === "time" ? formatPeriod(x, grain, true) : x || "(none)";
  const legend =
    series.length > 1 ? (
      <Legend
        mark={chart.type === "line" ? "line" : "square"}
        items={series.map((s, i) => ({ label: s.label, color: colors[i] }))}
      />
    ) : null;
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
            title={xLabel(String((payload?.[0]?.payload as Datum)?.x ?? ""))}
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
        <XAxis
          dataKey="x"
          {...X_AXIS_PROPS}
          tickFormatter={(x: string) => formatPeriod(x, grain)}
          minTickGap={24}
        />
        <YAxis
          {...Y_AXIS_PROPS}
          allowDecimals
          width={valueWidth}
          tickFormatter={tick}
        />
        {tooltip}
        {series.map((s, i) => (
          <Line
            key={s.key}
            dataKey={s.key}
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
            >
              {labelled && (
                <LabelList
                  dataKey={s.key}
                  position={horizontal ? "right" : "top"}
                  formatter={(v) => tick(v)}
                  style={{ fontSize: 11, fill: "var(--text-secondary)" }}
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
        aria-label={`${label}. ${describe(chart, series)}`}
        data-testid="widget-chart"
      >
        <ResponsiveContainer width="100%" height="100%">
          {plot}
        </ResponsiveContainer>
      </div>
    </div>
  );
}
