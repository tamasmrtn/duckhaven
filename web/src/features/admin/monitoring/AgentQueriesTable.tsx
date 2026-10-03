import { useNavigate } from "@tanstack/react-router";
import { X } from "lucide-react";
import { StatusPill } from "@/components/app/StatusPill";
import {
  DurationCell,
  SqlCell,
  formatDuration,
} from "@/components/app/queryTableCells";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAgentQueries } from "@/queries/agents";
import type { AgentQuerySort } from "@/types/agent";
import { AGENT_QUERY_SORTS } from "@/types/agent";
import { formatBytes } from "@/utils";

const SORT_LABEL: Record<AgentQuerySort, string> = {
  started_at: "Most recent",
  duration: "Longest",
  wait: "Longest wait",
  peak_memory: "Most memory",
  cpu_time: "Most CPU time",
  spill: "Most spill",
  bytes_read: "Most data read",
};

export interface TableScope {
  start: string;
  end: string;
  // A label when narrowed to one bucket, so the reader can see and undo it.
  bucket?: string;
}

function Mono({ children }: { children: string }) {
  return (
    <TableCell className="px-4 py-2 text-right font-mono text-xs text-text-secondary font-tabular">
      {children}
    </TableCell>
  );
}

/**
 * The runs behind the charts: every query alive during the range (or the clicked
 * bucket), sortable by what it cost. The step from "memory spiked at 14:05" to
 * "this query did it".
 */
export function AgentQueriesTable({
  ws,
  agentId,
  scope,
  sort,
  onSort,
  onClearBucket,
  finished,
}: {
  ws: string;
  agentId: string;
  scope: TableScope;
  sort: AgentQuerySort;
  onSort: (sort: AgentQuerySort) => void;
  onClearBucket: () => void;
  finished: number;
}) {
  const navigate = useNavigate();
  const { data, isLoading, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useAgentQueries(agentId, {
      start: scope.start,
      end: scope.end,
      sort,
      // Worst first: nulls (no profile, never started) sort last either way.
      dir: "desc",
    });
  const queries = data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <section
      className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)]"
      aria-label="Query runs"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border-subtle)] px-4 py-3">
        <div>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
            Query runs
          </h3>
          <p className="mt-0.5 text-2xs text-text-tertiary">
            {scope.bucket
              ? `Running or waiting during ${scope.bucket}.`
              : `Running or waiting during the range · ${finished} finished in it.`}{" "}
            Hover a duration for its wait/run split.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {scope.bucket && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              onClick={onClearBucket}
            >
              <X className="mr-1 size-3.5" />
              Whole range
            </Button>
          )}
          <Select
            value={sort}
            onValueChange={(v) => onSort(v as AgentQuerySort)}
          >
            <SelectTrigger
              className="h-7 w-40 text-xs"
              aria-label="sort queries"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {AGENT_QUERY_SORTS.map((s) => (
                <SelectItem key={s} value={s} className="text-xs">
                  {SORT_LABEL[s]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      {isLoading ? (
        <div className="space-y-1 p-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-10 w-full rounded" />
          ))}
        </div>
      ) : queries.length === 0 ? (
        <p className="px-4 py-8 text-center text-sm text-text-tertiary">
          No queries ran on this agent in this range.
        </p>
      ) : (
        <>
          <Table containerClassName="overflow-visible" className="text-sm">
            <TableHeader>
              <TableRow className="border-b border-[var(--border-subtle)] hover:bg-transparent">
                {[
                  ["Status", false],
                  ["SQL", false],
                  ["User", false],
                  ["Wait", true],
                  ["Duration", false],
                  ["Peak memory", true],
                  ["CPU time", true],
                  ["Spill", true],
                  ["Started", false],
                ].map(([h, numeric]) => (
                  <TableHead
                    key={String(h)}
                    className={`h-auto px-4 py-2 text-xs font-medium text-text-secondary ${
                      numeric ? "text-right" : "text-left"
                    }`}
                  >
                    {h}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {queries.map((q) => (
                <TableRow
                  key={q.id}
                  onClick={() =>
                    navigate({
                      to: "/$ws/queries/$queryId",
                      params: { ws, queryId: q.id },
                    })
                  }
                  className="cursor-pointer border-b border-[var(--border-subtle)] hover:bg-accent/50"
                >
                  <TableCell className="px-4 py-2">
                    <StatusPill status={q.status} durationMs={q.duration_ms} />
                  </TableCell>
                  <SqlCell query={q} />
                  <TableCell className="px-4 py-2 text-xs text-text-secondary">
                    {q.user_name ?? "—"}
                  </TableCell>
                  <Mono>{formatDuration(q.wait_ms)}</Mono>
                  <DurationCell query={q} />
                  <Mono>{formatBytes(q.peak_memory_bytes)}</Mono>
                  <Mono>
                    {q.cpu_time_ms == null
                      ? "—"
                      : formatDuration(Math.round(q.cpu_time_ms))}
                  </Mono>
                  <Mono>
                    {q.spill_bytes ? formatBytes(q.spill_bytes) : "—"}
                  </Mono>
                  <TableCell className="px-4 py-2 font-mono text-2xs text-text-tertiary">
                    {new Date(q.started_at).toLocaleString()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="flex items-center justify-between px-4 py-2 text-2xs text-text-tertiary">
            <span>{`${queries.length} shown`}</span>
            {hasNextPage && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                disabled={isFetchingNextPage}
                onClick={() => void fetchNextPage()}
              >
                {isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            )}
          </div>
        </>
      )}
    </section>
  );
}
