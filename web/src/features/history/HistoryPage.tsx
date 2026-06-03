import { useParams } from "@tanstack/react-router";
import { Clock } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusPill } from "@/components/app/StatusPill";
import { useWorkspaceQueries } from "@/queries/queries";
import { useAgents } from "@/queries/agents";
import { cn, shortId } from "@/utils";

function formatDuration(ms: number | null) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function HistoryPage() {
  const { ws } = useParams({ from: "/$ws/history" });
  const { data: wsQueries = [], isLoading } = useWorkspaceQueries(ws);
  const { data: agents = [] } = useAgents();
  const agentName = new Map(agents.map((a) => [a.id, a.name]));

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <h1 className="text-md font-semibold">History</h1>
      </div>

      <div className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="space-y-1 p-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton
                key={i}
                className="h-12 w-full animate-shimmer rounded"
              />
            ))}
          </div>
        ) : wsQueries.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <Clock className="size-8 text-text-tertiary" />
            <p className="text-md font-medium text-text-secondary">
              No queries yet.
            </p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-[var(--bg-surface)] z-10">
              <tr className="border-b border-[var(--border-subtle)]">
                {["Status", "SQL", "Agent", "Rows", "Duration", "Started"].map(
                  (h) => (
                    <th
                      key={h}
                      className="px-4 py-2 text-left text-xs font-medium text-text-secondary"
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {wsQueries.map((q, i) => (
                <tr
                  key={q.id}
                  className={cn(
                    "border-b border-[var(--border-subtle)] hover:bg-accent/50",
                    i % 2 === 0 ? "" : "bg-[var(--bg-surface)]/40",
                  )}
                >
                  <td className="px-4 py-2">
                    <StatusPill status={q.status} durationMs={q.duration_ms} />
                  </td>
                  <td className="px-4 py-2 max-w-xs">
                    <pre className="truncate font-mono text-xs text-text-primary">
                      {q.sql}
                    </pre>
                    {q.error && (
                      <p className="mt-0.5 text-2xs text-[var(--status-failed)] truncate">
                        {q.error}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-text-secondary">
                    {agentName.get(q.agent_id) ?? shortId(q.agent_id)}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-text-secondary font-tabular">
                    {q.row_count != null ? q.row_count.toLocaleString() : "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-text-secondary font-tabular">
                    {formatDuration(q.duration_ms)}
                  </td>
                  <td className="px-4 py-2 font-mono text-2xs text-text-tertiary">
                    {new Date(q.started_at).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
