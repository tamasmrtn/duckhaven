import { useNavigate, useParams } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StatusPill } from "@/components/app/StatusPill";
import {
  SessionStatusPill,
  formatCloseReason,
} from "@/components/app/SessionStatusPill";
import { useSqlSession, useSqlSessionStatements } from "@/queries/sql-sessions";
import { isLiveSession } from "@/types/sql-session";
import { formatClient } from "./SessionsPage";
import { cn, shortId } from "@/utils";

function formatDuration(ms: number | null) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatWhen(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-0.5">
      <p className="text-2xs font-medium uppercase tracking-wide text-text-tertiary">
        {label}
      </p>
      <p className="font-mono text-xs text-text-secondary break-all">{value}</p>
    </div>
  );
}

export function SessionDetailPage() {
  const { ws, sessionId } = useParams({ from: "/$ws/sessions/$sessionId" });
  const navigate = useNavigate();
  const { data: session, isLoading } = useSqlSession(sessionId);
  const live = session ? isLiveSession(session) : false;
  const { data: statements = [], isLoading: statementsLoading } =
    useSqlSessionStatements(sessionId, live);

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Connection"
        leading={
          <Button
            size="sm"
            variant="ghost"
            className="h-7 gap-1.5 px-2"
            onClick={() => navigate({ to: "/$ws/sessions", params: { ws } })}
          >
            <ArrowLeft className="size-3.5" />
            Connections
          </Button>
        }
        badge={session && <SessionStatusPill status={session.status} />}
      />

      <div className="flex-1 overflow-auto">
        {isLoading || !session ? (
          <div className="space-y-1 p-6">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton
                key={i}
                className="h-12 w-full animate-shimmer rounded"
              />
            ))}
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4 border-b border-[var(--border-subtle)] px-6 py-4 sm:grid-cols-4">
              <Field label="Principal" value={session.user_name ?? "—"} />
              <Field label="Client" value={formatClient(session)} />
              <Field
                label="Agent"
                value={
                  session.agent_name ??
                  (session.agent_id ? shortId(session.agent_id) : "—")
                }
              />
              <Field label="Catalog" value={session.active_catalog ?? "—"} />
              <Field label="Opened" value={formatWhen(session.opened_at)} />
              <Field
                label="Last active"
                value={formatWhen(session.last_active_at)}
              />
              <Field label="Closed" value={formatWhen(session.closed_at)} />
              <Field
                label="Ended because"
                value={formatCloseReason(session.close_reason) ?? "—"}
              />
              {session.staging_uri && (
                <div className="col-span-2 sm:col-span-4">
                  <Field label="Staging prefix" value={session.staging_uri} />
                </div>
              )}
              {session.error && (
                <div className="col-span-2 sm:col-span-4">
                  <p className="text-2xs font-medium uppercase tracking-wide text-text-tertiary">
                    Error
                  </p>
                  <p className="font-mono text-xs text-[var(--status-failed)]">
                    {session.error}
                  </p>
                </div>
              )}
            </div>

            {statementsLoading ? (
              <div className="space-y-1 p-6">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton
                    key={i}
                    className="h-10 w-full animate-shimmer rounded"
                  />
                ))}
              </div>
            ) : statements.length === 0 ? (
              <p className="px-6 py-10 text-center text-sm text-text-tertiary">
                This connection has not run any statements.
              </p>
            ) : (
              <Table containerClassName="overflow-visible" className="text-sm">
                <TableHeader className="sticky top-0 bg-[var(--bg-surface)] z-10">
                  <TableRow className="border-b border-[var(--border-subtle)] hover:bg-transparent">
                    {["#", "Status", "SQL", "Rows", "Duration", "Started"].map(
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
                  {statements.map((q, i) => (
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
                      <TableCell className="px-4 py-2 font-mono text-2xs text-text-tertiary font-tabular">
                        {i + 1}
                      </TableCell>
                      <TableCell className="px-4 py-2">
                        <StatusPill
                          status={q.status}
                          durationMs={q.duration_ms}
                        />
                      </TableCell>
                      <TableCell className="px-4 py-2 max-w-md">
                        <pre className="truncate font-mono text-xs text-text-primary">
                          {q.sql}
                        </pre>
                        {q.error && (
                          <p className="mt-0.5 truncate text-2xs text-[var(--status-failed)]">
                            {q.error}
                          </p>
                        )}
                      </TableCell>
                      <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary font-tabular">
                        {q.row_count != null
                          ? q.row_count.toLocaleString()
                          : "—"}
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
          </>
        )}
      </div>
    </div>
  );
}
