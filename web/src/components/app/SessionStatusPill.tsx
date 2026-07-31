import { cn } from "@/utils";
import type { SessionCloseReason, SqlSessionStatus } from "@/types/sql-session";

// Deliberately not `StatusPill`: that one is typed to QueryStatus, and a session
// moves through a different lifecycle. Same `--status-*` palette so the two read
// as one system.
const colors: Record<SqlSessionStatus, string> = {
  pending: "bg-[var(--status-queued)] text-white",
  opening: "bg-[var(--status-queued)] text-white",
  open: "bg-[var(--status-running)] text-white",
  closing: "bg-[var(--status-queued)] text-white",
  closed: "bg-[var(--status-success)] text-white",
  expired: "bg-[var(--status-cancelled)] text-white",
  failed: "bg-[var(--status-failed)] text-white",
};

const dots: Record<SqlSessionStatus, string> = {
  pending: "bg-white/80 animate-pulse",
  opening: "bg-white/80 animate-pulse",
  open: "bg-white/80 animate-pulse",
  closing: "bg-white/80 animate-pulse",
  closed: "bg-white/80",
  expired: "bg-white/80",
  failed: "bg-white/80",
};

// The server records the reason as a typed value; the UI is the only place it
// becomes prose. Never show the raw enum.
const CLOSE_REASONS: Record<SessionCloseReason, string> = {
  client: "closed by client",
  idle: "reaped — idle",
  max_lifetime: "reaped — max lifetime",
  open_timeout: "open timed out",
  compute_timeout: "compute did not start in time",
  provisioning_timeout: "no compute became available",
  agent_disconnect: "agent disconnected",
  agent_lease: "reaped by agent",
  failed: "failed to open",
};

export function formatCloseReason(
  reason: SessionCloseReason | null | undefined,
): string | null {
  if (!reason) return null;
  // A reason the server learned after this build shipped still reads better as
  // its own words than as nothing at all.
  return CLOSE_REASONS[reason] ?? String(reason).replace(/_/g, " ");
}

export function SessionStatusPill({ status }: { status: SqlSessionStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-2xs font-medium transition-colors duration-200",
        colors[status],
      )}
      role="status"
      aria-label={status}
      title={status}
    >
      <span className={cn("size-1.5 rounded-full", dots[status])} />
      {status}
    </span>
  );
}
