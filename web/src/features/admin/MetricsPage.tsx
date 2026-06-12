import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Activity } from "lucide-react";
import { EmptyState } from "@/components/app/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgentMetrics } from "@/queries/metrics";
import type { AgentMetrics } from "@/types/agent";
import { plural } from "@/utils";
import {
  formatAbsoluteTimestamp,
  formatRelativeTick,
  relativeMinuteTicks,
} from "./metricsTime";

const SERIES_COLORS = [
  "#3b82f6",
  "#f59e0b",
  "#10b981",
  "#ef4444",
  "#a855f7",
  "#06b6d4",
];

type MetricField = "cpu_percent" | "memory_percent";

// Merge per-agent sample arrays into a single time-keyed dataset with one column
// per agent, so recharts can draw a line per agent over a shared X axis.
function buildSeries(metrics: AgentMetrics[], field: MetricField) {
  const byTime = new Map<string, Record<string, number>>();
  for (const agent of metrics) {
    for (const sample of agent.samples) {
      // `t` is epoch ms so the x-axis is a true time scale (proportional
      // spacing + clean minute ticks), not a categorical per-sample axis.
      const row = byTime.get(sample.sampled_at) ?? {
        t: Date.parse(sample.sampled_at),
      };
      row[agent.name] = sample[field];
      byTime.set(sample.sampled_at, row);
    }
  }
  return Array.from(byTime.values()).sort((a, b) => a.t - b.t);
}

function UtilizationChart({
  title,
  metrics,
  field,
}: {
  title: string;
  metrics: AgentMetrics[];
  field: MetricField;
}) {
  const data = buildSeries(metrics, field);
  // Anchor "time ago" labels to the most recent sample, and place one tick per
  // minute so labels are distinct (no repeated "-2m -2m -2m").
  const refMs = data.length ? data[data.length - 1].t : 0;
  const minMs = data.length ? data[0].t : 0;
  const ticks = relativeMinuteTicks(minMs, refMs);
  return (
    <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
      <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-text-secondary">
        {title}
      </p>
      <div className="h-64" data-testid={`chart-${field}`}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data}>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke="var(--border-subtle)"
            />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              ticks={ticks}
              tickFormatter={(v) => formatRelativeTick(Number(v), refMs)}
              tick={{ fontSize: 11, fill: "var(--text-tertiary)" }}
            />
            <YAxis
              domain={[0, 100]}
              unit="%"
              tick={{ fontSize: 11, fill: "var(--text-tertiary)" }}
              width={44}
            />
            <Tooltip
              labelFormatter={(label) => formatAbsoluteTimestamp(Number(label))}
              formatter={(value) => `${value}%`}
            />
            {metrics.map((agent, i) => (
              <Line
                key={agent.agent_id}
                type="monotone"
                dataKey={agent.name}
                stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                dot={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function CurrentValueCard({ agent }: { agent: AgentMetrics }) {
  const latest = agent.samples[agent.samples.length - 1];
  return (
    <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
      <p className="text-sm font-medium">{agent.name}</p>
      <div className="mt-3 flex gap-6">
        <div>
          <p className="text-2xs uppercase tracking-wide text-text-tertiary">
            CPU
          </p>
          <p className="font-mono text-xl font-tabular">
            {latest ? `${latest.cpu_percent}%` : "—"}
          </p>
        </div>
        <div>
          <p className="text-2xs uppercase tracking-wide text-text-tertiary">
            Memory
          </p>
          <p className="font-mono text-xl font-tabular">
            {latest ? `${latest.memory_percent}%` : "—"}
          </p>
        </div>
      </div>
      {latest && (
        <p className="mt-3 text-2xs text-text-tertiary">
          Concurrency: {latest.active_profile}
        </p>
      )}
    </div>
  );
}

// Aggregate running/queued query counts from each agent's most recent sample.
function queryTotals(metrics: AgentMetrics[]) {
  return metrics.reduce(
    (acc, agent) => {
      const latest = agent.samples[agent.samples.length - 1];
      if (latest) {
        acc.running += latest.running_queries;
        acc.queued += latest.queued_queries;
      }
      return acc;
    },
    { running: 0, queued: 0 },
  );
}

function QueryCounter({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
      <p className="text-2xs uppercase tracking-wide text-text-tertiary">
        {label}
      </p>
      <p className="font-mono text-3xl font-tabular">{value}</p>
    </div>
  );
}

export function MetricsPage() {
  const { data: metrics = [], isLoading } = useAgentMetrics();
  const totals = queryTotals(metrics);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-6 py-3 shrink-0">
        <p className="text-xs text-text-secondary font-tabular">
          {plural(metrics.length, "agent")} reporting
        </p>
      </div>

      <div className="flex-1 overflow-auto p-4">
        {isLoading ? (
          <div className="space-y-4">
            <Skeleton className="h-24 w-full rounded-lg" />
            <Skeleton className="h-64 w-full rounded-lg" />
            <Skeleton className="h-64 w-full rounded-lg" />
          </div>
        ) : metrics.length === 0 ? (
          <EmptyState
            icon={Activity}
            title="No live utilization"
            description="Connect an agent to see real-time CPU and memory usage."
          />
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <QueryCounter label="Running queries" value={totals.running} />
              <QueryCounter label="Queued queries" value={totals.queued} />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {metrics.map((agent) => (
                <CurrentValueCard key={agent.agent_id} agent={agent} />
              ))}
            </div>
            <UtilizationChart
              title="CPU utilization"
              metrics={metrics}
              field="cpu_percent"
            />
            <UtilizationChart
              title="Memory utilization"
              metrics={metrics}
              field="memory_percent"
            />
          </div>
        )}
      </div>
    </div>
  );
}
