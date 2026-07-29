import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type {
  ActivityPoint,
  ActivityState,
  AgentMonitoring,
} from "@/types/agent";
import { useIsDark } from "@/hooks/useIsDark";
import { formatDuration } from "../metricsTime";
import { ACTIVITY, QUEUE_DEPTH, resolve, seriesColor } from "./chartColors";
import {
  ChartFrame,
  GRID_PROPS,
  Legend,
  TOOLTIP_PROPS,
  Y_AXIS_PROPS,
  timeAxisProps,
} from "./ChartFrame";

const ms = (iso: string) => Date.parse(iso);

// 2px of surface between touching marks, per the mark spec: neighbours read as
// separate because of the gap, not because of a stroke drawn around them.
const SURFACE_GAP = 2;
const BAR_RADIUS: [number, number, number, number] = [4, 4, 0, 0];

/** Peak concurrent queries — the saturation chart. */
export function PeakQueryCountChart({ data }: { data: AgentMonitoring }) {
  const dark = useIsDark();
  const running = resolve(QUEUE_DEPTH.running, dark);
  const queued = resolve(QUEUE_DEPTH.queued, dark);
  const rows = data.peak_query_count.map((p) => ({ ...p, t: ms(p.t) }));
  const peakRunning = Math.max(0, ...rows.map((r) => r.running));
  const peakQueued = Math.max(0, ...rows.map((r) => r.queued));

  return (
    <ChartFrame
      title="Peak query count"
      subtitle="Highest concurrent depth the agent reported in each bucket."
      testId="chart-peak-query-count"
      legend={
        <Legend
          items={[
            { label: "Peak running", color: running, value: `${peakRunning}` },
            { label: "Peak queued", color: queued, value: `${peakQueued}` },
          ]}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} barCategoryGap={SURFACE_GAP}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...timeAxisProps(ms(data.start), ms(data.end))} />
          <YAxis {...Y_AXIS_PROPS} allowDecimals={false} />
          <Tooltip {...TOOLTIP_PROPS} />
          {/* Queued sits on top of running: the reader's question is "did work
              pile up", and a stack answers it by total height. */}
          <Bar
            dataKey="running"
            name="Running"
            stackId="depth"
            fill={running}
            maxBarSize={24}
            isAnimationActive={false}
          />
          <Bar
            dataKey="queued"
            name="Queued"
            stackId="depth"
            fill={queued}
            maxBarSize={24}
            radius={BAR_RADIUS}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

/** Throughput — queries finishing per minute. */
export function CompletedQueryCountChart({ data }: { data: AgentMonitoring }) {
  const dark = useIsDark();
  const rows = data.completed_query_count.map((p) => ({ ...p, t: ms(p.t) }));

  return (
    <ChartFrame
      title="Completed query count"
      subtitle="Queries per minute, including failed and cancelled runs."
      testId="chart-completed-query-count"
    >
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...timeAxisProps(ms(data.start), ms(data.end))} />
          <YAxis
            {...Y_AXIS_PROPS}
            // Rounding the domain up to a whole query keeps the ticks whole too.
            // Left to itself recharts divides the exact data max into fifths and
            // labels them 0.65 / 1.3 / 1.95 — precision nobody reads on an axis.
            domain={[0, (max: number) => Math.max(1, Math.ceil(max))]}
          />
          <Tooltip
            {...TOOLTIP_PROPS}
            cursor={{ stroke: "var(--border-strong)", strokeWidth: 1 }}
            formatter={(value) => [`${value}/min`, "Completed"]}
          />
          {/* One series, so no legend box — the title already names it. */}
          <Line
            type="monotone"
            dataKey="per_minute"
            name="Completed"
            stroke={seriesColor(0, dark)}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

const ACTIVITY_LABEL: Record<ActivityState, string> = {
  query: "Query activity",
  other: "Other activity",
  ready: "Ready",
  starting: "Starting",
  down: "Not running",
  unknown: "No data",
};

const ACTIVITY_HELP: Record<ActivityState, string> = {
  query: "Queries were running or queued.",
  other: "Up with no queries — held SQL sessions or result fetching.",
  ready: "Up and idle. This is the time an idle timeout reclaims.",
  starting: "Provisioning; not yet accepting work.",
  down: "No agent running.",
  unknown: "Before this agent started recording lifecycle events.",
};

// Order matters: the legend reads busiest to quietest, which is the same order
// the single-hue ramp steps through, so the colour ordering is self-explaining.
const ACTIVITY_ORDER: ActivityState[] = [
  "query",
  "other",
  "ready",
  "starting",
  "down",
  "unknown",
];

/**
 * When the agent was up, and what it was doing — the idle-vs-busy chart.
 *
 * Drawn as full-height bars of a constant value rather than a proper band chart:
 * each bucket is one categorical state, so the only visual variable is colour, and
 * a constant-height bar keeps the marks on the same time scale as the charts above
 * without inventing a y-axis nobody reads.
 */
export function ActivityChart({ data }: { data: AgentMonitoring }) {
  const dark = useIsDark();
  const rows = data.activity.map((p: ActivityPoint) => ({
    t: ms(p.t),
    state: p.state,
    v: 1,
  }));
  const present = ACTIVITY_ORDER.filter((s) =>
    data.activity.some((p) => p.state === s),
  );
  const bucketCounts = ACTIVITY_ORDER.reduce<Record<string, number>>(
    (acc, state) => {
      acc[state] = data.activity.filter((p) => p.state === state).length;
      return acc;
    },
    {},
  );

  const busy = data.summary.busy_ratio;
  const idleTimeout = data.summary.idle_timeout_minutes;

  return (
    <ChartFrame
      title="Agent activity"
      height="h-16"
      subtitle={
        <>
          {`Up ${formatDuration(data.summary.uptime_s)}`}
          {busy !== null && ` · ${Math.round(busy * 100)}% busy`}
          {idleTimeout !== null && ` · idle timeout ${idleTimeout} min`}
          {busy !== null && busy < 0.25 && idleTimeout !== null && (
            <span className="ml-1 text-[var(--status-running)]">
              — mostly idle while up; a shorter idle timeout would reclaim it.
            </span>
          )}
        </>
      }
      testId="chart-activity"
      legend={
        <Legend
          items={present.map((state) => ({
            label: ACTIVITY_LABEL[state],
            color: resolve(ACTIVITY[state], dark),
            value: `${bucketCounts[state]}`,
          }))}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} barCategoryGap={SURFACE_GAP}>
          <XAxis {...timeAxisProps(ms(data.start), ms(data.end))} />
          <YAxis hide domain={[0, 1]} />
          <Tooltip
            {...TOOLTIP_PROPS}
            formatter={(_v, _n, item) => {
              const state = item?.payload?.state as ActivityState;
              return [ACTIVITY_HELP[state], ACTIVITY_LABEL[state]];
            }}
          />
          <Bar dataKey="v" isAnimationActive={false} radius={[2, 2, 2, 2]}>
            {rows.map((row) => (
              <Cell
                key={row.t}
                fill={resolve(ACTIVITY[row.state], dark)}
                // The one state whose colour alone would be ambiguous also gets a
                // hatch, so "we have no record" can never be mistaken for "it was off".
                {...(row.state === "unknown"
                  ? { fillOpacity: 0.8, stroke: "var(--border-subtle)" }
                  : {})}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

/** Why runs failed, over time. */
export function FailuresChart({ data }: { data: AgentMonitoring }) {
  const dark = useIsDark();
  // Reasons keep a stable slot for the whole window, so a colour never changes
  // meaning when a reason appears or disappears from a later bucket.
  const reasons = Array.from(
    new Set(data.failures.map((f) => f.reason)),
  ).sort();
  const totals = Object.fromEntries(
    reasons.map((r) => [
      r,
      data.failures
        .filter((f) => f.reason === r)
        .reduce((sum, f) => sum + f.count, 0),
    ]),
  );

  const byBucket = new Map<number, Record<string, number>>();
  for (const failure of data.failures) {
    const t = ms(failure.t);
    const row = byBucket.get(t) ?? { t };
    row[failure.reason] = (row[failure.reason] ?? 0) + failure.count;
    byBucket.set(t, row);
  }
  const rows = Array.from(byBucket.values()).sort((a, b) => a.t - b.t);

  if (!reasons.length) return null;

  return (
    <ChartFrame
      title="Failures & rejections"
      subtitle="Failed and cancelled runs, by cause."
      testId="chart-failures"
      legend={
        <Legend
          items={reasons.map((reason, i) => ({
            label: reason.replace(/_/g, " "),
            color: seriesColor(i, dark),
            value: `${totals[reason]}`,
          }))}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} barCategoryGap={SURFACE_GAP}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...timeAxisProps(ms(data.start), ms(data.end))} />
          <YAxis {...Y_AXIS_PROPS} />
          <Tooltip {...TOOLTIP_PROPS} />
          {reasons.map((reason, i) => (
            <Bar
              key={reason}
              dataKey={reason}
              name={reason.replace(/_/g, " ")}
              stackId="failures"
              fill={seriesColor(i, dark)}
              maxBarSize={24}
              radius={i === reasons.length - 1 ? BAR_RADIUS : undefined}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

/**
 * CPU and memory over the window, from the per-minute rollup.
 *
 * Each series is a line at the bucket's average with a wash up to its peak. The
 * peak is the point: a query that exhausts memory and dies does so in under a
 * second, and averaging that minute buries it — an agent that touched 90% reads
 * as a calm 7% line. The band is what makes a spike that caused a failure
 * visible next to the failure itself.
 */
export function UtilizationChart({ data }: { data: AgentMonitoring }) {
  const dark = useIsDark();
  const rows = data.utilization.map((p) => ({
    ...p,
    t: ms(p.t),
    // Recharts draws a range area from a [low, high] pair. Null when the bucket
    // went unmeasured, so the band breaks with the line rather than collapsing
    // to the axis.
    cpu_band: p.cpu_avg === null ? null : [p.cpu_avg, p.cpu_max ?? p.cpu_avg],
    mem_band: p.mem_avg === null ? null : [p.mem_avg, p.mem_max ?? p.mem_avg],
  }));
  const cpu = seriesColor(0, dark);
  const mem = seriesColor(1, dark);
  const peakCpu = Math.max(0, ...rows.map((r) => r.cpu_max ?? 0));
  const peakMem = Math.max(0, ...rows.map((r) => r.mem_max ?? 0));

  return (
    <ChartFrame
      title="Utilization"
      subtitle="Line is the bucket average, shading its peak. Gaps are buckets the agent reported nothing in."
      testId="chart-utilization"
      legend={
        <Legend
          items={[
            { label: "CPU peak", color: cpu, value: `${Math.round(peakCpu)}%` },
            {
              label: "Memory peak",
              color: mem,
              value: `${Math.round(peakMem)}%`,
            },
          ]}
        />
      }
    >
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={rows}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis {...timeAxisProps(ms(data.start), ms(data.end))} />
          <YAxis {...Y_AXIS_PROPS} domain={[0, 100]} unit="%" width={44} />
          <Tooltip
            {...TOOLTIP_PROPS}
            cursor={{ stroke: "var(--border-strong)", strokeWidth: 1 }}
            formatter={(value, name) => {
              if (value == null) return ["—", name];
              // The band's value is the [avg, peak] pair it was built from.
              if (Array.isArray(value)) return [`${value[1]}%`, name];
              return [`${value}%`, name];
            }}
          />
          {/* Bands first so the average lines draw over them. A wash, not a
              saturated block — it is context for the line, not a second series. */}
          <Area
            dataKey="cpu_band"
            name="CPU peak"
            stroke="none"
            fill={cpu}
            fillOpacity={0.15}
            connectNulls={false}
            isAnimationActive={false}
            activeDot={false}
          />
          <Area
            dataKey="mem_band"
            name="Memory peak"
            stroke="none"
            fill={mem}
            fillOpacity={0.15}
            connectNulls={false}
            isAnimationActive={false}
            activeDot={false}
          />
          <Line
            type="monotone"
            dataKey="cpu_avg"
            name="CPU avg"
            stroke={cpu}
            strokeWidth={2}
            dot={false}
            // Null means "not measured": break the line rather than drawing
            // through a zero the agent never reported.
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="mem_avg"
            name="Memory avg"
            stroke={mem}
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
