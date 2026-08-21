import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { QueryProfileNode, QueryProfileSummary } from "@/types/query";
import { cn, formatBytes } from "@/utils";
import { BADGE_LABELS, type NodeBadge, nodeBadges } from "./highlights";

const BADGE_CLASS: Record<NodeBadge, string> = {
  scan: "bg-[var(--status-failed)]/15 text-[var(--status-failed)]",
  estimate: "bg-[var(--brand-orange)]/15 text-[var(--brand-orange)]",
  time: "bg-[var(--brand-yellow)]/20 text-[var(--brand-orange)]",
};

function formatMs(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 1 : 0)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function rowsLabel(node: QueryProfileNode): string {
  const scanned = node.rows_scanned;
  const produced = node.rows_produced;
  const fmt = (n: number | null) => (n == null ? "—" : n.toLocaleString());
  if (scanned && scanned > 0) return `${fmt(scanned)} → ${fmt(produced)}`;
  return `${fmt(produced)} rows`;
}

function ProfileNodeRow({
  node,
  summary,
  depth,
}: {
  node: QueryProfileNode;
  summary: QueryProfileSummary;
  depth: number;
}) {
  const [open, setOpen] = useState(true);
  const [showInfo, setShowInfo] = useState(false);
  const hasChildren = node.children.length > 0;
  const badges = nodeBadges(node, summary);
  const timePct =
    summary.latency_ms > 0 && node.time_ms != null
      ? Math.min(100, (node.time_ms / summary.latency_ms) * 100)
      : 0;
  const infoEntries = Object.entries(node.extra_info ?? {});

  return (
    <div>
      <div
        className="flex items-center gap-2 border-b border-[var(--border-subtle)] py-1.5 pr-3 hover:bg-accent/40"
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className={cn(
            "shrink-0 text-text-tertiary",
            !hasChildren && "invisible",
          )}
          aria-label={open ? "Collapse" : "Expand"}
        >
          {open ? (
            <ChevronDown className="size-3.5" />
          ) : (
            <ChevronRight className="size-3.5" />
          )}
        </button>
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-text-primary">
          {node.name || node.type}
        </span>
        {badges.map((b) => (
          <span
            key={b}
            className={cn(
              "shrink-0 rounded px-1.5 py-0.5 text-2xs font-medium",
              BADGE_CLASS[b],
            )}
            title={BADGE_LABELS[b]}
          >
            {BADGE_LABELS[b]}
          </span>
        ))}
        <span className="w-40 shrink-0 text-right font-mono text-2xs text-text-secondary font-tabular">
          {rowsLabel(node)}
        </span>
        <span className="w-16 shrink-0 text-right font-mono text-2xs text-text-tertiary font-tabular">
          {formatBytes(node.result_bytes)}
        </span>
        <div className="flex w-24 shrink-0 items-center gap-1.5">
          <div className="h-1.5 flex-1 overflow-hidden rounded bg-[var(--bg-elevated)]">
            <div
              className="h-full rounded bg-[var(--brand-yellow)]"
              style={{ width: `${timePct}%` }}
            />
          </div>
          <span className="w-10 text-right font-mono text-2xs text-text-tertiary font-tabular">
            {formatMs(node.time_ms)}
          </span>
        </div>
        {infoEntries.length > 0 && (
          <button
            type="button"
            onClick={() => setShowInfo((v) => !v)}
            className="shrink-0 text-2xs text-text-tertiary hover:text-text-secondary"
            aria-label="Toggle operator details"
          >
            {showInfo ? "hide" : "info"}
          </button>
        )}
      </div>
      {showInfo && infoEntries.length > 0 && (
        <div
          className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] py-1.5 pr-3 text-2xs text-text-secondary"
          style={{ paddingLeft: `${depth * 16 + 28}px` }}
        >
          {infoEntries.map(([k, v]) => (
            <div key={k} className="font-mono">
              <span className="text-text-tertiary">{k}: </span>
              {Array.isArray(v) ? v.join(", ") : String(v)}
            </div>
          ))}
        </div>
      )}
      {open &&
        node.children.map((child, i) => (
          <ProfileNodeRow
            key={`${child.type}-${i}`}
            node={child}
            summary={summary}
            depth={depth + 1}
          />
        ))}
    </div>
  );
}

export function ProfileTree({
  tree,
  summary,
}: {
  tree: QueryProfileNode;
  summary: QueryProfileSummary;
}) {
  return (
    <div className="overflow-auto">
      <ProfileNodeRow node={tree} summary={summary} depth={0} />
    </div>
  );
}
