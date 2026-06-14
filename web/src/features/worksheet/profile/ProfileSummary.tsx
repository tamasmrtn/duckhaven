import type { QueryProfileSummary } from "@/types/query";
import { formatBytes } from "@/utils";
import { isSpilled } from "./highlights";

function formatMs(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 1 : 0)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-2xs uppercase tracking-wide text-text-tertiary">
        {label}
      </span>
      <span className="font-mono text-xs text-text-primary font-tabular">
        {value}
      </span>
    </div>
  );
}

export function ProfileSummary({ summary }: { summary: QueryProfileSummary }) {
  return (
    <div className="flex flex-wrap items-start gap-x-6 gap-y-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-4 py-3">
      <Stat label="Latency" value={formatMs(summary.latency_ms)} />
      <Stat label="CPU time" value={formatMs(summary.cpu_time_ms)} />
      <Stat label="Rows" value={summary.rows_returned.toLocaleString()} />
      <Stat label="Result" value={formatBytes(summary.result_bytes)} />
      <Stat
        label="Peak memory"
        value={formatBytes(summary.peak_memory_bytes)}
      />
      {summary.reserved_memory_bytes != null && (
        <Stat
          label="Reserved mem"
          value={formatBytes(summary.reserved_memory_bytes)}
        />
      )}
      {summary.reserved_threads != null && summary.reserved_threads > 0 && (
        <Stat
          label="Reserved CPU"
          value={`${summary.reserved_threads} ${
            summary.reserved_threads === 1 ? "thread" : "threads"
          }`}
        />
      )}
      <div className="flex flex-col gap-0.5">
        <span className="text-2xs uppercase tracking-wide text-text-tertiary">
          Spill
        </span>
        <span
          className={
            isSpilled(summary)
              ? "font-mono text-xs font-tabular text-[var(--status-failed)]"
              : "font-mono text-xs font-tabular text-text-primary"
          }
        >
          {formatBytes(summary.spill_bytes)}
        </span>
      </div>
      <Stat label="Read" value={formatBytes(summary.bytes_read)} />
      <Stat label="Written" value={formatBytes(summary.bytes_written)} />
    </div>
  );
}
