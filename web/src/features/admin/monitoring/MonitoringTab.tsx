import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Activity } from "lucide-react";
import { EmptyState } from "@/components/ui/empty-state";
import { StatusPill } from "@/components/app/StatusPill";
import { DurationCell, SqlCell } from "@/components/app/queryTableCells";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAgentMonitoring } from "@/queries/agents";
import { useAgentMetrics } from "@/queries/metrics";
import { useWorkspaceQueries } from "@/queries/queries";
import type { Agent, MonitoringWindow } from "@/types/agent";
import { MONITORING_WINDOWS } from "@/types/agent";
import { cn } from "@/utils";
import {
  ActivityChart,
  CompletedQueryCountChart,
  FailuresChart,
  PeakQueryCountChart,
  UtilizationChart,
} from "./MonitoringCharts";

const WINDOW_LABEL: Record<MonitoringWindow, string> = {
  "1h": "Last 1 hour",
  "3h": "Last 3 hours",
  "8h": "Last 8 hours",
  "12h": "Last 12 hours",
  "24h": "Last 24 hours",
};

/**
 * A single instantaneous number.
 *
 * Fed by the 2s in-memory ring buffer rather than the windowed rollup: "how many
 * queries are running *right now*" is the one question on this page that a
 * minute-grained series genuinely cannot answer.
 */
function LiveStat({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-4 py-3">
      <p className="text-2xs uppercase tracking-wide text-text-tertiary">
        {label}
      </p>
      <p
        className={cn("mt-1 font-mono text-xl font-tabular", tone)}
        data-testid={`live-${label.toLowerCase().replace(/\s+/g, "-")}`}
      >
        {value}
      </p>
    </div>
  );
}

function LiveStatistics({ agent }: { agent: Agent }) {
  const { data: metrics = [] } = useAgentMetrics();
  const mine = metrics.find((m) => m.agent_id === agent.id);
  const latest = mine?.samples[mine.samples.length - 1];
  const size =
    agent.requested_cpu != null && agent.requested_memory_gb != null
      ? `${agent.requested_cpu} vCPU · ${agent.requested_memory_gb} GB`
      : agent.capabilities
        ? `${agent.capabilities.cores} cores · ${agent.capabilities.memory_limit_gb} GB`
        : "—";

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <LiveStat
        label="Status"
        value={
          agent.lifecycle && agent.lifecycle !== "running"
            ? agent.lifecycle
            : agent.status
        }
        tone={
          agent.status === "healthy"
            ? "text-[var(--status-success)]"
            : "text-[var(--status-failed)]"
        }
      />
      <LiveStat
        label="Running queries"
        value={latest ? `${latest.running_queries}` : "—"}
      />
      <LiveStat
        label="Queued queries"
        value={latest ? `${latest.queued_queries}` : "—"}
      />
      <LiveStat label="Size" value={size} />
    </div>
  );
}

/** Runs on this agent inside the selected window. */
function WindowedHistory({
  ws,
  agentId,
  since,
  until,
}: {
  ws: string;
  agentId: string;
  since: string;
  until: string;
}) {
  const navigate = useNavigate();
  // Agents are global, so an agent-scoped list has to span workspaces; the server
  // gates that on the admin permission this whole page already requires.
  // A bounded window on one agent, so it takes the first page and stops: no
  // Load more here. It still has to read `items` off the envelope.
  const { items: queries, isLoading } = useWorkspaceQueries(ws, {
    all_workspaces: true,
    agent_id: agentId,
    since,
    until,
  });

  return (
    <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
      <div className="border-b border-[var(--border-subtle)] px-4 py-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
          History
        </h3>
        <p className="mt-0.5 text-2xs text-text-tertiary">
          Runs <em>started</em> on this agent in the selected window — the
          charts above count runs as they <em>finish</em>, so a run spanning the
          edge of the window can appear in one and not the other. Hover a
          duration for the queued/running split.
        </p>
      </div>
      {isLoading ? (
        <div className="space-y-1 p-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full rounded" />
          ))}
        </div>
      ) : queries.length === 0 ? (
        <p className="px-4 py-8 text-center text-sm text-text-tertiary">
          No queries ran on this agent in this window.
        </p>
      ) : (
        <Table containerClassName="overflow-visible" className="text-sm">
          <TableHeader>
            <TableRow className="border-b border-[var(--border-subtle)] hover:bg-transparent">
              {["Status", "SQL", "User", "Rows", "Duration", "Started"].map(
                (h) => (
                  <TableHead
                    key={h}
                    className="h-auto px-4 py-2 text-left text-xs font-medium text-text-secondary"
                  >
                    {h}
                  </TableHead>
                ),
              )}
            </TableRow>
          </TableHeader>
          <TableBody>
            {queries.map((q, i) => (
              <TableRow
                key={q.id}
                onClick={() =>
                  navigate({
                    to: "/$ws/queries/$queryId",
                    params: { ws, queryId: q.id },
                  })
                }
                className={cn(
                  "cursor-pointer border-b border-[var(--border-subtle)] hover:bg-accent/50",
                  i % 2 === 0 ? "" : "bg-[var(--bg-surface)]/40",
                )}
              >
                <TableCell className="px-4 py-2">
                  <StatusPill status={q.status} durationMs={q.duration_ms} />
                </TableCell>
                <SqlCell query={q} />
                <TableCell className="px-4 py-2 text-xs text-text-secondary">
                  {q.user_name ?? "—"}
                </TableCell>
                <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary font-tabular">
                  {q.row_count != null ? q.row_count.toLocaleString() : "—"}
                </TableCell>
                <DurationCell query={q} />
                <TableCell className="px-4 py-2 font-mono text-2xs text-text-tertiary">
                  {new Date(q.started_at).toLocaleString()}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}

export function MonitoringTab({ ws, agent }: { ws: string; agent: Agent }) {
  const [window, setWindow] = useState<MonitoringWindow>("8h");
  const { data, isLoading, isFetching } = useAgentMonitoring(agent.id, window);

  return (
    <div className="space-y-4">
      <LiveStatistics agent={agent} />

      <div className="flex items-center gap-2">
        <Select
          value={window}
          onValueChange={(v) => setWindow(v as MonitoringWindow)}
        >
          <SelectTrigger className="h-8 w-44 text-xs" aria-label="time range">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {MONITORING_WINDOWS.map((w) => (
              <SelectItem key={w} value={w} className="text-xs">
                {WINDOW_LABEL[w]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-2xs text-text-tertiary" aria-live="polite">
          {data
            ? `${data.bucket_seconds / 60}-minute buckets`
            : "Loading buckets…"}
        </p>
      </div>

      {isLoading || !data ? (
        <div className="space-y-4">
          <Skeleton className="h-56 w-full rounded-lg" />
          <Skeleton className="h-56 w-full rounded-lg" />
          <Skeleton className="h-24 w-full rounded-lg" />
        </div>
      ) : (
        // Dim rather than unmount while the next window loads, so switching the
        // filter never collapses the page back to skeletons.
        <div
          className={cn(
            "space-y-4 transition-opacity",
            isFetching && "opacity-60",
          )}
        >
          <PeakQueryCountChart data={data} />
          <CompletedQueryCountChart data={data} />
          <ActivityChart data={data} />
          <FailuresChart data={data} />
          <UtilizationChart data={data} />
          {data.summary.completed === 0 && data.summary.failed === 0 && (
            <EmptyState
              icon={Activity}
              title="No queries in this window"
              description="Widen the time range, or run something on this agent."
            />
          )}
          <WindowedHistory
            ws={ws}
            agentId={agent.id}
            since={data.start}
            until={data.end}
          />
        </div>
      )}
    </div>
  );
}
