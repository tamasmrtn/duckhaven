import type { ReactNode } from "react";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { AgentMonitoring, MonitoringBucket } from "@/types/agent";
import { formatDuration } from "../metricsTime";
import { LOAD, OUTCOME, RESOURCE, TIMELINE, resolve } from "./chartColors";
import {
  ChartFrame,
  GRID_PROPS,
  Legend,
  TOOLTIP_PROPS,
  Y_AXIS_PROPS,
  timeAxisProps,
} from "./ChartFrame";

/** One bucket, with its position on the shared time axis. */
export interface BucketRow extends MonitoringBucket {
  x: number; // bucket start, epoch ms
  mid: number; // bucket midpoint: where a bar for the whole bucket is centred
  end: number;
}

export interface ChartHandlers {
  onMouseDown: (state: { activeTooltipIndex?: unknown }) => void;
  onMouseMove: (state: { activeTooltipIndex?: unknown }) => void;
  onMouseUp: (state: { activeTooltipIndex?: unknown }) => void;
  onMouseLeave: () => void;
}

/** Everything a panel needs, computed once for the whole stack. */
export interface PanelContext {
  data: AgentMonitoring;
  rows: BucketRow[];
  // `rows` plus a closing point at the range end: a step line drawn "after"
  // each point needs it, or the last bucket gets no width.
  stepRows: BucketRow[];
  startMs: number;
  endMs: number;
  downSpans: [number, number][];
  // The bucket still filling in now, if the range ends at "now".
  live: [number, number] | null;
  selected: [number, number] | null;
  dragging: [number, number] | null;
  handlers: ChartHandlers;
  bucketLabel: string; // "5-minute", "2-hour"
  cores: number | null;
  memoryGb: number | null;
  dark: boolean;
}

export function toRows(data: AgentMonitoring): {
  rows: BucketRow[];
  stepRows: BucketRow[];
} {
  const bucketMs = data.bucket_seconds * 1000;
  const rows = data.buckets.map((b) => {
    const x = Date.parse(b.t);
    const end = x + b.seconds * 1000;
    // Bars sit at the *nominal* bucket centre, even for a first or last bucket
    // the range cuts short: Recharts sizes every bar from the closest pair of
    // points, and a short bucket's true centre would halve them all.
    const mid = Math.floor(x / bucketMs) * bucketMs + bucketMs / 2;
    return { ...b, x, end, mid };
  });
  const last = rows[rows.length - 1];
  const stepRows = last
    ? [...rows, { ...last, x: last.end, mid: last.end }]
    : rows;
  return { rows, stepRows };
}

const NOT_MEASURED = "—";

/** A zero drawn as no mark, so it also drops out of the tooltip's list. */
function orNothing(value: number): number | undefined {
  return value ? value : undefined;
}

/**
 * Context drawn behind every panel: the stretches the agent was not running, the
 * bucket still in progress, the selected bucket and a drag in flight. Returned as
 * an array of reference areas so each chart can spread it among its children.
 */
function backdrop(ctx: PanelContext, yMax?: number): ReactNode[] {
  const down = resolve(TIMELINE.down, ctx.dark);
  const areas: ReactNode[] = ctx.downSpans.map(([x1, x2]) => (
    <ReferenceArea
      key={`down-${x1}`}
      x1={x1}
      x2={x2}
      y2={yMax}
      fill={down}
      fillOpacity={0.6}
      stroke="none"
      ifOverflow="hidden"
    />
  ));
  if (ctx.live) {
    areas.push(
      <ReferenceArea
        key="live"
        x1={ctx.live[0]}
        x2={ctx.live[1]}
        y2={yMax}
        fill="var(--border-subtle)"
        fillOpacity={0.35}
        stroke="var(--border-strong)"
        strokeDasharray="3 3"
        ifOverflow="hidden"
      />,
    );
  }
  for (const [key, span, opacity] of [
    ["selected", ctx.selected, 0.25],
    ["dragging", ctx.dragging, 0.35],
  ] as const) {
    if (span) {
      areas.push(
        <ReferenceArea
          key={key}
          x1={span[0]}
          x2={span[1]}
          y2={yMax}
          fill="var(--accent)"
          fillOpacity={opacity}
          stroke="none"
          ifOverflow="hidden"
        />,
      );
    }
  }
  return areas;
}

// Not synced: a shared tooltip opened one box per panel, each covering the next.
// The panels already share one time axis, which is what lines them up.
function chartProps(ctx: PanelContext) {
  return {
    ...ctx.handlers,
    style: { cursor: "crosshair" },
  };
}

function sum(rows: BucketRow[], pick: (r: BucketRow) => number): number {
  return rows.reduce((total, r) => total + pick(r), 0);
}

function pct(value: number | null | undefined): string {
  return value == null ? NOT_MEASURED : `${Math.round(value)}%`;
}

// ── Timeline ────────────────────────────────────────────────────────────────

const STATES = [
  { key: "busy_s", label: "Busy", color: TIMELINE.busy },
  { key: "idle_s", label: "Idle", color: TIMELINE.idle },
  { key: "starting_s", label: "Starting", color: TIMELINE.starting },
  { key: "down_s", label: "Not running", color: TIMELINE.down },
  { key: "unknown_s", label: "No record", color: TIMELINE.unknown },
] as const;

/**
 * Where the agent's time went, per bucket: the share it was busy (a query was
 * running), idle, starting, not running, or with no lifecycle record. Exact
 * seconds from the API, so a bucket that was busy for ten seconds shows a sliver,
 * not a full bar.
 */
export function TimelinePanel({ ctx }: { ctx: PanelContext }) {
  const rows = ctx.rows.map((r) => {
    const out: Record<string, number | undefined> = { x: r.mid };
    for (const s of STATES) {
      out[s.key] = r.seconds ? orNothing(r[s.key] / r.seconds) : undefined;
    }
    return out;
  });
  const totals = STATES.map((s) => ({
    ...s,
    total: sum(ctx.rows, (r) => r[s.key]),
  })).filter((s) => s.total > 0);

  return (
    <ChartFrame
      title="Timeline"
      subtitle="Share of each bucket the agent was busy (a query running), idle, starting or not running."
      height="h-16"
      testId="chart-timeline"
      legend={
        <Legend
          items={totals.map((s) => ({
            label: s.label,
            color: resolve(s.color, ctx.dark),
            value: formatDuration(s.total),
          }))}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} barCategoryGap={0} {...chartProps(ctx)}>
          <XAxis {...timeAxisProps(ctx.startMs, ctx.endMs)} hide />
          <YAxis hide domain={[0, 1]} />
          <Tooltip
            {...TOOLTIP_PROPS}
            formatter={(value, name) => [
              `${Math.round(Number(value) * 100)}%`,
              name,
            ]}
          />
          {backdrop(ctx, 1)}
          {STATES.map((s) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.label}
              stackId="time"
              fill={resolve(s.color, ctx.dark)}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ── Queries ─────────────────────────────────────────────────────────────────

function platformFailures(failed: Record<string, number>): number {
  return Object.entries(failed).reduce(
    (total, [reason, n]) => (reason === "sql_error" ? total : total + n),
    0,
  );
}

/** Queries that finished in each bucket, by outcome. */
export function QueriesPanel({ ctx }: { ctx: PanelContext }) {
  const rows = ctx.rows.map((r) => ({
    x: r.mid,
    failed: orNothing(platformFailures(r.failed)),
    cancelled: orNothing(r.cancelled),
    sql_error: orNothing(r.failed.sql_error ?? 0),
    done: orNothing(r.done),
    causes: r.failed,
  }));
  const series = [
    { key: "failed", label: "Failed", color: OUTCOME.failed },
    { key: "cancelled", label: "Cancelled", color: OUTCOME.cancelled },
    { key: "sql_error", label: "SQL error", color: OUTCOME.sql_error },
    { key: "done", label: "Succeeded", color: OUTCOME.done },
  ] as const;
  const causes = Object.entries(ctx.data.summary.failed_by_reason).filter(
    ([reason]) => reason !== "sql_error",
  );

  return (
    <ChartFrame
      title="Queries"
      subtitle={
        <>
          {`Finished per ${ctx.bucketLabel} bucket. "SQL error" is a mistake in the query itself; "Failed" is the platform's.`}
          {causes.length > 0 && (
            <span className="ml-1 text-text-secondary">
              Failed by cause:{" "}
              {causes
                .map(([reason, n]) => `${reason.replace(/_/g, " ")} ${n}`)
                .join(" · ")}
            </span>
          )}
        </>
      }
      testId="chart-queries"
      legend={
        <Legend
          items={series.map((s) => ({
            label: s.label,
            color: resolve(s.color, ctx.dark),
            value: `${rows.reduce((total, r) => total + (r[s.key] ?? 0), 0)}`,
          }))}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} barCategoryGap={2} {...chartProps(ctx)}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...timeAxisProps(ctx.startMs, ctx.endMs)} />
          <YAxis {...Y_AXIS_PROPS} allowDecimals={false} />
          <Tooltip
            {...TOOLTIP_PROPS}
            formatter={(value, name, item) => {
              if (name !== "Failed" || !value) return [value, name];
              const detail = Object.entries(
                (item?.payload?.causes ?? {}) as Record<string, number>,
              )
                .filter(([reason]) => reason !== "sql_error")
                .map(([reason, n]) => `${reason.replace(/_/g, " ")} ${n}`)
                .join(", ");
              return [`${value} (${detail})`, name];
            }}
          />
          {backdrop(ctx)}
          {series.map((s, i) => (
            <Bar
              key={s.key}
              dataKey={s.key}
              name={s.label}
              stackId="outcome"
              fill={resolve(s.color, ctx.dark)}
              maxBarSize={24}
              radius={i === series.length - 1 ? [4, 4, 0, 0] : undefined}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ── Concurrency ─────────────────────────────────────────────────────────────

/**
 * How many queries were running and waiting, on average over each bucket, with
 * the true peak. The averages are exact (query-seconds over bucket seconds), so
 * they stack; the waiting layers are the saturation signal.
 */
export function ConcurrencyPanel({ ctx }: { ctx: PanelContext }) {
  const seconds = sum(ctx.rows, (r) => r.seconds) || 1;
  const avg = (key: "running_avg" | "queued_avg" | "compute_wait_avg") =>
    (sum(ctx.rows, (r) => r[key] * r.seconds) / seconds).toFixed(2);
  const wait = ctx.data.summary.wait_p95_ms;
  const layers = [
    { key: "running_avg", label: "Running", color: LOAD.running },
    { key: "queued_avg", label: "Waiting to run", color: LOAD.queued },
    {
      key: "compute_wait_avg",
      label: "Waiting for compute",
      color: LOAD.compute,
    },
  ] as const;

  return (
    <ChartFrame
      title="Concurrency"
      subtitle={`Average queries in each state per ${ctx.bucketLabel} bucket; the dashed line is the most running at any instant. p95 wait before running: ${
        wait == null ? NOT_MEASURED : formatDuration(wait / 1000)
      }.`}
      testId="chart-concurrency"
      legend={
        <Legend
          items={[
            ...layers.map((l) => ({
              label: `${l.label} (avg)`,
              color: resolve(l.color, ctx.dark),
              value: avg(l.key),
            })),
            {
              label: "Peak running",
              color: "var(--text-secondary)",
              value: `${ctx.data.summary.peak_running}`,
            },
          ]}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={ctx.stepRows} {...chartProps(ctx)}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...timeAxisProps(ctx.startMs, ctx.endMs)} />
          <YAxis {...Y_AXIS_PROPS} allowDecimals />
          <Tooltip
            {...TOOLTIP_PROPS}
            formatter={(value, name, item) => {
              if (name === "Peak running") return [value, name];
              const row = item?.payload as BucketRow | undefined;
              const suffix =
                name === "Waiting to run" && row?.wait_p95_ms != null
                  ? ` (p95 wait ${formatDuration(row.wait_p95_ms / 1000)})`
                  : "";
              return [`${Number(value).toFixed(2)}${suffix}`, name];
            }}
          />
          {backdrop(ctx)}
          {layers.map((l) => (
            <Area
              key={l.key}
              type="stepAfter"
              dataKey={l.key}
              name={l.label}
              stackId="load"
              stroke="none"
              fill={resolve(l.color, ctx.dark)}
              fillOpacity={0.85}
              isAnimationActive={false}
              activeDot={false}
            />
          ))}
          <Line
            type="stepAfter"
            dataKey="peak_running"
            name="Peak running"
            stroke="var(--text-secondary)"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            dot={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

// ── CPU and memory ──────────────────────────────────────────────────────────

function ResourcePanel({
  ctx,
  title,
  subtitle,
  avgKey,
  maxKey,
  peak,
  color,
  testId,
  markers,
}: {
  ctx: PanelContext;
  title: string;
  subtitle: string;
  avgKey: "cpu_avg" | "mem_avg";
  maxKey: "cpu_max" | "mem_max";
  peak: number | null;
  color: string;
  testId: string;
  markers?: ReactNode[];
}) {
  const rows = ctx.stepRows.map((r) => ({
    ...r,
    // A [low, high] pair draws the peak band; null breaks it with the line.
    band: r[avgKey] == null ? null : [r[avgKey], r[maxKey] ?? r[avgKey]],
  }));

  return (
    <ChartFrame
      title={title}
      subtitle={subtitle}
      testId={testId}
      // One series, so no identity legend: just its headline number.
      legend={<Legend items={[{ label: "Peak", color, value: pct(peak) }]} />}
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows} {...chartProps(ctx)}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...timeAxisProps(ctx.startMs, ctx.endMs)} />
          <YAxis {...Y_AXIS_PROPS} domain={[0, 100]} unit="%" width={44} />
          <Tooltip
            {...TOOLTIP_PROPS}
            formatter={(value, name) => {
              if (value == null) return [NOT_MEASURED, name];
              if (Array.isArray(value)) return [pct(value[1]), "Peak"];
              return [pct(Number(value)), name];
            }}
          />
          {backdrop(ctx, 100)}
          {markers}
          <Area
            type="stepAfter"
            dataKey="band"
            name="Peak"
            stroke="none"
            fill={color}
            fillOpacity={0.18}
            connectNulls={false}
            isAnimationActive={false}
            activeDot={false}
          />
          <Line
            type="stepAfter"
            dataKey={avgKey}
            name="Average"
            stroke={color}
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

export function CpuPanel({ ctx }: { ctx: PanelContext }) {
  const of = ctx.cores ? ` of ${ctx.cores} vCPU` : "";
  return (
    <ResourcePanel
      ctx={ctx}
      title="CPU"
      subtitle={`%${of}. Line: average per ${ctx.bucketLabel} bucket. Band: highest 2-second reading. Gaps: nothing measured.`}
      avgKey="cpu_avg"
      maxKey="cpu_max"
      peak={ctx.data.summary.cpu_peak}
      color={resolve(RESOURCE.cpu, ctx.dark)}
      testId="chart-cpu"
    />
  );
}

/**
 * Memory, with a marker wherever the kernel killed a process for it or a query
 * failed out of memory — the event the peak band exists to explain.
 */
export function MemoryPanel({ ctx }: { ctx: PanelContext }) {
  const of = ctx.memoryGb ? ` of ${ctx.memoryGb} GB` : "";
  const ooms = ctx.rows.filter(
    (r) => (r.oom_kills ?? 0) > 0 || (r.failed.out_of_memory ?? 0) > 0,
  );
  const markers = ooms.map((r) => (
    <ReferenceLine
      key={`oom-${r.x}`}
      x={r.mid}
      stroke="var(--status-failed)"
      strokeWidth={1.5}
      label={{
        value: "OOM",
        position: "insideTopRight",
        fontSize: 10,
        fill: "var(--status-failed)",
      }}
    />
  ));
  return (
    <ResourcePanel
      ctx={ctx}
      title="Memory"
      subtitle={`%${of}. Line: average per ${ctx.bucketLabel} bucket. Band: highest level reached, including between samples.${
        ooms.length ? " OOM: out-of-memory kill or failure." : ""
      }`}
      avgKey="mem_avg"
      maxKey="mem_max"
      peak={ctx.data.summary.mem_peak}
      color={resolve(RESOURCE.memory, ctx.dark)}
      testId="chart-memory"
      markers={markers}
    />
  );
}
