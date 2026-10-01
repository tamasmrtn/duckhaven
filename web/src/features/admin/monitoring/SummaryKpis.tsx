import type { MonitoringSummary } from "@/types/agent";
import { formatDuration } from "../metricsTime";

function Kpi({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-2xs uppercase tracking-wide text-text-tertiary">
        {label}
      </dt>
      <dd className="mt-0.5 font-mono text-md font-tabular text-text-primary">
        {value}
        {detail && (
          <span className="ml-1.5 font-sans text-2xs text-text-tertiary">
            {detail}
          </span>
        )}
      </dd>
    </div>
  );
}

const DASH = "—";

/**
 * The whole range in one line, before any chart. Every figure is exact: busy and
 * up time are measured from query and lifecycle intervals, so they read the same
 * whatever the zoom level.
 */
export function SummaryKpis({ summary }: { summary: MonitoringSummary }) {
  const busy =
    summary.busy_ratio == null
      ? DASH
      : `${Math.round(summary.busy_ratio * 100)}%`;
  return (
    <dl
      className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-4 py-3 sm:grid-cols-3 lg:grid-cols-6"
      aria-label="Range summary"
    >
      <Kpi label="Up" value={formatDuration(summary.uptime_s)} />
      <Kpi
        label="Busy"
        value={busy}
        detail={
          summary.busy_ratio == null
            ? undefined
            : formatDuration(summary.busy_s)
        }
      />
      <Kpi
        label="Idle"
        value={summary.uptime_s ? formatDuration(summary.idle_s) : DASH}
      />
      <Kpi
        label="Queries"
        value={`${summary.finished}`}
        detail={summary.failed ? `${summary.failed} failed` : undefined}
      />
      <Kpi
        label="p95 wait"
        value={
          summary.wait_p95_ms == null
            ? DASH
            : formatDuration(summary.wait_p95_ms / 1000)
        }
        detail={summary.wait_n ? `of ${summary.wait_n}` : undefined}
      />
      <Kpi
        label="Peak memory"
        value={
          summary.mem_peak == null ? DASH : `${Math.round(summary.mem_peak)}%`
        }
      />
    </dl>
  );
}
