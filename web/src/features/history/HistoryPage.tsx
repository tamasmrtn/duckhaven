import { useState } from "react";
import { useNavigate, useParams, useSearch } from "@tanstack/react-router";
import { Clock } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StatusPill } from "@/components/app/StatusPill";
import { useWorkspaceQueries } from "@/queries/queries";
import { useAgents } from "@/queries/agents";
import { useMe } from "@/queries/auth";
import { useWorkspaces } from "@/queries/workspaces";
import { cn, shortId } from "@/utils";

function formatDuration(ms: number | null) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

// Interactive runs are stored with a null origin, so "interactive" is the
// server's spelling for that case rather than a real column value.
type OriginFilter = "all" | "interactive" | "scheduled" | "session";

const ORIGIN_FILTERS: { value: OriginFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "interactive", label: "Interactive" },
  { value: "scheduled", label: "Scheduled" },
  { value: "session", label: "Session" },
];

export function HistoryPage() {
  const { ws } = useParams({ from: "/$ws/history" });
  const { agent: agentFilter } = useSearch({ from: "/$ws/history" });
  const navigate = useNavigate();
  const { data: me } = useMe();
  const isAdmin = me?.role === "admin";

  const [allWorkspaces, setAllWorkspaces] = useState(false);
  const [userFilter, setUserFilter] = useState("");
  const [origin, setOrigin] = useState<OriginFilter>("all");
  const trimmed = userFilter.trim();
  // Cross-workspace + user filtering are admin-only affordances; a non-admin
  // never sends them, so the endpoint only ever returns their workspace.
  // Filtering by agent spans workspaces (an agent is global), so fetch all when
  // an agent filter is active and an admin is viewing.
  const all = isAdmin && (allWorkspaces || !!agentFilter);
  // The agent filter goes to the server rather than being applied to the page it
  // returns: that page is capped, so filtering it here showed nothing at all for an
  // agent whose runs were older than the most recent hundred queries cluster-wide.
  const { data: wsQueries = [], isLoading } = useWorkspaceQueries(ws, {
    all_workspaces: all,
    user_id: all && trimmed ? trimmed : undefined,
    origin: origin === "all" ? undefined : origin,
    agent_id: all && agentFilter ? agentFilter : undefined,
  });
  const { data: agents = [] } = useAgents();
  const { data: workspaces = [] } = useWorkspaces();
  const agentName = new Map(agents.map((a) => [a.id, a.name]));
  const workspaceSlug = new Map(workspaces.map((w) => [w.id, w.slug]));

  function openProfile(queryId: string) {
    navigate({ to: "/$ws/queries/$queryId", params: { ws, queryId } });
  }

  // Only worth a column when something in view actually belongs to a session,
  // so the ordinary history table keeps its existing shape.
  const anySession = wsQueries.some((q) => q.session_id);

  const columns = all
    ? [
        "Status",
        "Workspace",
        "Agent",
        "User",
        "SQL",
        "Rows",
        "Duration",
        "Started",
      ]
    : [
        "Status",
        "SQL",
        "Agent",
        "User",
        ...(anySession ? ["Session"] : []),
        "Rows",
        "Duration",
        "Started",
      ];

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <h1 className="text-md font-semibold">History</h1>
        {agentFilter && (
          <button
            type="button"
            onClick={() =>
              navigate({ to: "/$ws/history", params: { ws }, search: {} })
            }
            className="flex items-center gap-1 rounded-full border border-[var(--border-subtle)] bg-accent/50 px-2 py-0.5 text-xs text-text-secondary hover:text-text-primary"
            aria-label="clear agent filter"
          >
            Agent: {agentName.get(agentFilter) ?? shortId(agentFilter)} ✕
          </button>
        )}
        <div className="ml-auto flex items-center gap-3">
          <div
            className="flex rounded-md border border-[var(--border-subtle)] p-0.5 text-xs"
            role="group"
            aria-label="filter by origin"
          >
            {ORIGIN_FILTERS.map(({ value, label }) => (
              <button
                key={value}
                type="button"
                onClick={() => setOrigin(value)}
                aria-pressed={origin === value}
                className={cn(
                  "rounded px-2 py-1 transition-colors",
                  origin === value
                    ? "bg-accent text-text-primary font-medium"
                    : "text-text-secondary hover:text-text-primary",
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {isAdmin && (
          <div className="flex items-center gap-3">
            {all && (
              <Input
                aria-label="filter by user id"
                placeholder="filter by user id"
                value={userFilter}
                onChange={(e) => setUserFilter(e.target.value)}
                className="h-7 w-64 text-xs"
              />
            )}
            <div className="flex rounded-md border border-[var(--border-subtle)] p-0.5 text-xs">
              <button
                type="button"
                onClick={() => setAllWorkspaces(false)}
                aria-pressed={!allWorkspaces}
                className={cn(
                  "rounded px-2 py-1 transition-colors",
                  !allWorkspaces
                    ? "bg-accent text-text-primary font-medium"
                    : "text-text-secondary hover:text-text-primary",
                )}
              >
                This workspace
              </button>
              <button
                type="button"
                onClick={() => setAllWorkspaces(true)}
                aria-pressed={allWorkspaces}
                className={cn(
                  "rounded px-2 py-1 transition-colors",
                  allWorkspaces
                    ? "bg-accent text-text-primary font-medium"
                    : "text-text-secondary hover:text-text-primary",
                )}
              >
                All workspaces
              </button>
            </div>
          </div>
        )}
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
          <Table containerClassName="overflow-visible" className="text-sm">
            <TableHeader className="sticky top-0 bg-[var(--bg-surface)] z-10">
              <TableRow className="border-b border-[var(--border-subtle)] hover:bg-transparent">
                {columns.map((h) => (
                  <TableHead
                    key={h}
                    className="h-auto px-4 py-2 text-left text-xs font-medium text-text-secondary"
                  >
                    {h}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {wsQueries.map((q, i) => (
                <TableRow
                  key={q.id}
                  onClick={all ? undefined : () => openProfile(q.id)}
                  className={cn(
                    "border-b border-[var(--border-subtle)] hover:bg-accent/50",
                    all ? "" : "cursor-pointer",
                    i % 2 === 0 ? "" : "bg-[var(--bg-surface)]/40",
                  )}
                >
                  <TableCell className="px-4 py-2">
                    <StatusPill status={q.status} durationMs={q.duration_ms} />
                  </TableCell>
                  {all && (
                    <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary">
                      {workspaceSlug.get(q.workspace_id) ??
                        shortId(q.workspace_id)}
                    </TableCell>
                  )}
                  {!all && (
                    <TableCell className="px-4 py-2 max-w-xs">
                      <pre className="truncate font-mono text-xs text-text-primary">
                        {q.sql}
                      </pre>
                      {q.error && (
                        <p className="mt-0.5 text-2xs text-[var(--status-failed)] truncate">
                          {q.error}
                        </p>
                      )}
                    </TableCell>
                  )}
                  <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary">
                    {agentName.get(q.agent_id) ?? shortId(q.agent_id)}
                  </TableCell>
                  <TableCell className="px-4 py-2 text-xs text-text-secondary">
                    {q.user_name ?? "—"}
                  </TableCell>
                  {!all && anySession && (
                    <TableCell className="px-4 py-2 text-xs">
                      {q.session_id ? (
                        <button
                          type="button"
                          aria-label={`session ${shortId(q.session_id)}`}
                          onClick={(e) => {
                            // The row itself opens the query profile.
                            e.stopPropagation();
                            navigate({
                              to: "/$ws/sessions/$sessionId",
                              params: { ws, sessionId: q.session_id! },
                            });
                          }}
                          className="font-mono text-xs text-text-secondary underline-offset-2 hover:text-text-primary hover:underline"
                        >
                          {shortId(q.session_id)}
                        </button>
                      ) : (
                        <span className="text-text-tertiary">—</span>
                      )}
                    </TableCell>
                  )}
                  {all && (
                    <TableCell className="px-4 py-2 max-w-xs">
                      <pre className="truncate font-mono text-xs text-text-primary">
                        {q.sql}
                      </pre>
                      {q.error && (
                        <p className="mt-0.5 text-2xs text-[var(--status-failed)] truncate">
                          {q.error}
                        </p>
                      )}
                    </TableCell>
                  )}
                  <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary font-tabular">
                    {q.row_count != null ? q.row_count.toLocaleString() : "—"}
                  </TableCell>
                  <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary font-tabular">
                    {formatDuration(q.duration_ms)}
                  </TableCell>
                  <TableCell className="px-4 py-2 font-mono text-2xs text-text-tertiary">
                    {new Date(q.started_at).toLocaleString()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}
