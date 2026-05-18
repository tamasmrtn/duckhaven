import { useEffect, useState } from "react";
import { cn } from "@/utils";
import type { QueryStatus } from "@/types/query";

interface StatusPillProps {
  status: QueryStatus;
  startedAt?: string;
  durationMs?: number | null;
}

const colors: Record<QueryStatus, string> = {
  queued: "bg-[var(--status-queued)] text-white",
  running: "bg-[var(--status-running)] text-white",
  done: "bg-[var(--status-success)] text-white",
  failed: "bg-[var(--status-failed)] text-white",
  cancelled: "bg-[var(--status-cancelled)] text-white",
};

const dots: Record<QueryStatus, string> = {
  queued: "bg-white/80",
  running: "bg-white/80 animate-pulse",
  done: "bg-white/80",
  failed: "bg-white/80",
  cancelled: "bg-white/80",
};

const labels: Record<QueryStatus, string> = {
  queued: "queued",
  running: "running",
  done: "done",
  failed: "failed",
  cancelled: "cancelled",
};

function useElapsed(startedAt?: string, active = false) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!active || !startedAt) return;
    const start = new Date(startedAt).getTime();
    const tick = () => setElapsed(Math.floor((Date.now() - start) / 1000));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startedAt, active]);

  return elapsed;
}

function formatDuration(ms: number) {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function StatusPill({ status, startedAt, durationMs }: StatusPillProps) {
  const elapsed = useElapsed(startedAt, status === "running");

  const suffix =
    status === "running"
      ? ` ${elapsed}s`
      : status === "done" && durationMs != null
        ? ` ${formatDuration(durationMs)}`
        : "";

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-2xs font-medium transition-colors duration-200",
        colors[status],
      )}
      role="status"
      aria-live="polite"
    >
      <span className={cn("size-1.5 rounded-full", dots[status])} />
      {labels[status]}
      {suffix}
    </span>
  );
}
