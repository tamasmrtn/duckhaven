import { useState } from "react";
import { ClipboardList } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { StatusPill } from "@/components/app/StatusPill";
import { useAuditLog } from "@/queries/queries";
import { useAgents } from "@/queries/agents";
import { useWorkspaces } from "@/queries/workspaces";
import { cn, shortId } from "@/utils";

function formatDuration(ms: number | null) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function AuditPage() {
  const [userFilter, setUserFilter] = useState("");
  const trimmed = userFilter.trim();
  const { data: queries = [], isLoading } = useAuditLog(
    trimmed ? { user_id: trimmed } : undefined,
  );
  const { data: agents = [] } = useAgents();
  const { data: workspaces = [] } = useWorkspaces();
  const agentName = new Map(agents.map((a) => [a.id, a.name]));
  const workspaceSlug = new Map(workspaces.map((w) => [w.id, w.slug]));

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--border-subtle)] px-6 py-3 shrink-0">
        <p className="text-xs text-text-secondary font-tabular">
          {queries.length === 1 ? "1 entry" : `${queries.length} entries`}
        </p>
        <Input
          aria-label="filter by user id"
          placeholder="filter by user id"
          value={userFilter}
          onChange={(e) => setUserFilter(e.target.value)}
          className="h-7 w-64 text-xs"
        />
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
        ) : queries.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <ClipboardList className="size-8 text-text-tertiary" />
            <p className="text-md font-medium text-text-secondary">
              No queries yet.
            </p>
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-[var(--bg-surface)] z-10">
              <tr className="border-b border-[var(--border-subtle)]">
                {[
                  "Status",
                  "Workspace",
                  "Agent",
                  "SQL",
                  "Rows",
                  "Duration",
                  "Started",
                ].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-2 text-left text-xs font-medium text-text-secondary"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {queries.map((q, i) => (
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
                  <td className="px-4 py-2 font-mono text-xs text-text-secondary">
                    {workspaceSlug.get(q.workspace_id) ??
                      shortId(q.workspace_id)}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs text-text-secondary">
                    {agentName.get(q.agent_id) ?? shortId(q.agent_id)}
                  </td>
                  <td className="px-4 py-2 max-w-xs">
                    <pre className="truncate font-mono text-xs">{q.sql}</pre>
                  </td>
                  <td className="px-4 py-2 font-mono text-xs font-tabular">
                    {q.row_count != null ? q.row_count.toLocaleString() : "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs font-tabular">
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
