import { useState } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import { Plug } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  SessionStatusPill,
  formatCloseReason,
} from "@/components/app/SessionStatusPill";
import { ApiError } from "@/api/client";
import { useSqlSessions, useCloseSqlSession } from "@/queries/sql-sessions";
import { useMe } from "@/queries/auth";
import type { SqlSession } from "@/types/sql-session";
import { cn, shortId } from "@/utils";

export function formatClient(session: SqlSession): string {
  if (!session.client_name) return "—";
  return session.client_version
    ? `${session.client_name} ${session.client_version}`
    : session.client_name;
}

function formatWhen(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

export function SessionsPage() {
  const { ws } = useParams({ from: "/$ws/sessions" });

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <h1 className="text-md font-semibold">Connections</h1>
      </div>

      <Tabs defaultValue="live" className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center justify-between px-6 pt-3">
          <TabsList>
            <TabsTrigger value="live">Live</TabsTrigger>
            <TabsTrigger value="all">All</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="live" className="mt-3 min-h-0 flex-1 overflow-auto">
          <SessionsTable ws={ws} live />
        </TabsContent>
        <TabsContent value="all" className="mt-3 min-h-0 flex-1 overflow-auto">
          <SessionsTable ws={ws} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function EmptyState({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      <Plug className="size-8 text-text-tertiary" />
      <p className="text-md font-medium text-text-secondary">{title}</p>
      <p className="text-sm text-text-tertiary">{subtitle}</p>
    </div>
  );
}

function SessionsTable({ ws, live = false }: { ws: string; live?: boolean }) {
  const navigate = useNavigate();
  const { data: me } = useMe();
  const isAdmin = (me?.permissions?.length ?? 0) > 0;
  const {
    data: sessions = [],
    isLoading,
    error,
  } = useSqlSessions(ws, { live });
  const [confirming, setConfirming] = useState<SqlSession | null>(null);

  // Sessions are gated on SQL_SESSIONS_ENABLED; when off, every endpoint 404s.
  // Say so plainly rather than rendering a generic failure.
  if (error instanceof ApiError && error.status === 404) {
    return (
      <EmptyState
        title="SQL connections are not enabled."
        subtitle="Set SQL_SESSIONS_ENABLED=true on the API to let dbt and dlt open connections."
      />
    );
  }

  if (isLoading) {
    return (
      <div className="space-y-1 px-6">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full animate-shimmer rounded" />
        ))}
      </div>
    );
  }

  if (sessions.length === 0) {
    return live ? (
      <EmptyState
        title="No live connections."
        subtitle="An open connection holds an agent slot for its whole life; none are held right now."
      />
    ) : (
      <EmptyState
        title="No connections yet."
        subtitle="Connections opened by dbt, dlt, or the SQL connector appear here, newest first."
      />
    );
  }

  const columns = [
    "Status",
    "Principal",
    "Client",
    "Agent",
    "Catalog",
    "Statements",
    "Opened",
    live ? "Last active" : "Ended because",
    ...(live && isAdmin ? [""] : []),
  ];

  return (
    <>
      <Table containerClassName="overflow-visible" className="text-sm">
        <TableHeader className="sticky top-0 bg-[var(--bg-surface)] z-10">
          <TableRow className="border-b border-[var(--border-subtle)] hover:bg-transparent">
            {columns.map((h, i) => (
              <TableHead
                key={h || `actions-${i}`}
                className="h-auto px-4 py-2 text-left text-xs font-medium text-text-secondary"
              >
                {h}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {sessions.map((s, i) => (
            <TableRow
              key={s.id}
              onClick={() =>
                navigate({
                  to: "/$ws/sessions/$sessionId",
                  params: { ws, sessionId: s.id },
                })
              }
              className={cn(
                "cursor-pointer border-b border-[var(--border-subtle)] hover:bg-accent/50",
                i % 2 === 0 ? "" : "bg-[var(--bg-surface)]/40",
              )}
            >
              <TableCell className="px-4 py-2">
                <SessionStatusPill status={s.status} />
              </TableCell>
              <TableCell className="px-4 py-2 text-xs text-text-secondary">
                {s.user_name ?? "—"}
              </TableCell>
              <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary">
                {formatClient(s)}
              </TableCell>
              <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary">
                {s.agent_name ?? (s.agent_id ? shortId(s.agent_id) : "—")}
              </TableCell>
              <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary">
                {s.active_catalog ?? "—"}
              </TableCell>
              <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary font-tabular">
                {(s.statement_count ?? 0).toLocaleString()}
              </TableCell>
              <TableCell className="px-4 py-2 font-mono text-2xs text-text-tertiary">
                {formatWhen(s.opened_at ?? s.created_at)}
              </TableCell>
              <TableCell className="px-4 py-2 text-2xs text-text-tertiary">
                {live ? (
                  <span className="font-mono">
                    {formatWhen(s.last_active_at)}
                  </span>
                ) : (
                  (formatCloseReason(s.close_reason) ?? "—")
                )}
              </TableCell>
              {live && isAdmin && (
                <TableCell className="px-4 py-2 text-right">
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-6 text-2xs"
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirming(s);
                    }}
                  >
                    Force close
                  </Button>
                </TableCell>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <ForceCloseDialog
        ws={ws}
        session={confirming}
        onClose={() => setConfirming(null)}
      />
    </>
  );
}

function ForceCloseDialog({
  ws,
  session,
  onClose,
}: {
  ws: string;
  session: SqlSession | null;
  onClose: () => void;
}) {
  const close = useCloseSqlSession(ws);

  async function handleClose() {
    if (!session) return;
    await close.mutateAsync(session.id);
    onClose();
  }

  return (
    <Dialog open={session !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Force close this connection?</DialogTitle>
          <DialogDescription>
            The agent drops its held connection and frees the admission slot.
            Anything the client still has in flight fails, and it must open a
            new connection to continue.
          </DialogDescription>
        </DialogHeader>
        {session && (
          <p className="py-2 text-sm text-text-secondary">
            {formatClient(session)} · opened by {session.user_name ?? "unknown"}{" "}
            on {session.agent_name ?? "an agent"}.
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleClose}
            disabled={close.isPending}
          >
            Force close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
