import { TableCell } from "@/components/ui/table";
import type { Query } from "@/types/query";

/**
 * Cells shared by the query tables — workspace history and the per-agent
 * monitoring tab.
 *
 * The two tables carry different column sets (one spans workspaces and agents,
 * the other is already scoped to one agent), so they are not one component. What
 * they must not diverge on is how a run is *rendered*: a duration, a SQL snippet
 * and its error should read identically wherever a run appears.
 */

export function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/**
 * Total wall-clock, with the queued/running split on hover.
 *
 * `duration_ms` is the agent's execution time, so a run that waited in the
 * admission queue looks fast by it while having felt slow. The title carries the
 * breakdown — the difference between "the query is slow" and "the agent was
 * busy", which are different fixes.
 */
type Timed = Pick<
  Query,
  "started_at" | "running_at" | "finished_at" | "duration_ms"
>;

export function DurationCell({ query }: { query: Timed }) {
  return (
    <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary font-tabular">
      <span title={durationBreakdown(query)}>
        {formatDuration(query.duration_ms)}
      </span>
    </TableCell>
  );
}

export function durationBreakdown(query: Timed): string {
  const submitted = Date.parse(query.started_at);
  const started = query.running_at ? Date.parse(query.running_at) : null;
  if (started === null || Number.isNaN(started)) {
    // Pre-dates the column, or the run never started.
    return query.duration_ms == null
      ? "Not started"
      : `Running ${formatDuration(query.duration_ms)}`;
  }
  const queuedMs = Math.max(0, started - submitted);
  const finished = query.finished_at ? Date.parse(query.finished_at) : null;
  const runningMs =
    query.duration_ms ??
    (finished !== null && !Number.isNaN(finished)
      ? Math.max(0, finished - started)
      : null);
  return `Queued ${formatDuration(queuedMs)} · running ${formatDuration(runningMs)}`;
}

/** The SQL, truncated, with any tags and its error beneath. */
export function SqlCell({
  query,
}: {
  query: Pick<Query, "sql" | "error"> & Partial<Pick<Query, "tags">>;
}) {
  const tags = Object.entries(query.tags ?? {});
  return (
    <TableCell className="px-4 py-2 max-w-xs">
      <pre className="truncate font-mono text-xs text-text-primary">
        {query.sql}
      </pre>
      {tags.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1" aria-label="tags">
          {tags.map(([key, value]) => (
            <span
              key={key}
              className="rounded border border-[var(--border-subtle)] px-1 font-mono text-2xs text-text-secondary"
            >
              {key}={value}
            </span>
          ))}
        </div>
      )}
      {query.error && (
        <p className="mt-0.5 text-2xs text-[var(--status-failed)] truncate">
          {query.error}
        </p>
      )}
    </TableCell>
  );
}
