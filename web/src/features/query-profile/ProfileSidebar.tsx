import type { QueryProfileNode, QueryProfileSummary } from "@/types/query";
import { cn, formatBytes } from "@/utils";
import {
  BADGE_LABELS,
  type NodeBadge,
  isSpilled,
  nodeBadges,
} from "@/features/worksheet/profile/highlights";
import type { GraphLayout } from "./layout";

function fmtMs(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 1 : 0)} ms`;
  return `${(ms / 1000).toFixed(2)} s`;
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-2xs font-semibold uppercase tracking-wide text-text-tertiary">
        {title}
      </h3>
      {children}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-xs text-text-secondary">{label}</span>
      <span className="font-mono text-xs text-text-primary font-tabular">
        {value}
      </span>
    </div>
  );
}

function NodeDetail({
  node,
  summary,
}: {
  node: QueryProfileNode;
  summary: QueryProfileSummary;
}) {
  const pct =
    summary.latency_ms > 0 && node.time_ms != null
      ? ` (${Math.round((node.time_ms / summary.latency_ms) * 100)}%)`
      : "";
  const extra = Object.entries(node.extra_info ?? {}).filter(
    ([k]) => k !== "Estimated Cardinality",
  );
  return (
    <Section title="Operator">
      <div className="font-mono text-sm font-medium text-text-primary">
        {node.type}
      </div>
      <div className="flex flex-col gap-1">
        {node.rows_scanned ? (
          <Row
            label="Rows scanned"
            value={node.rows_scanned.toLocaleString()}
          />
        ) : null}
        <Row
          label="Rows produced"
          value={
            node.rows_produced != null
              ? node.rows_produced.toLocaleString()
              : "—"
          }
        />
        <Row
          label="Estimated"
          value={
            node.estimated_cardinality != null
              ? node.estimated_cardinality.toLocaleString()
              : "—"
          }
        />
        <Row label="Time" value={`${fmtMs(node.time_ms)}${pct}`} />
        <Row label="Result size" value={formatBytes(node.result_bytes)} />
      </div>
      {extra.length > 0 && (
        <div className="mt-1 flex flex-col gap-0.5 border-t border-[var(--border-subtle)] pt-2">
          {extra.map(([k, v]) => (
            <div key={k} className="font-mono text-2xs text-text-secondary">
              <span className="text-text-tertiary">{k}: </span>
              {Array.isArray(v) ? v.join(", ") : String(v)}
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

function MostExpensive({
  layout,
  summary,
  selectedId,
  onSelect,
}: {
  layout: GraphLayout;
  summary: QueryProfileSummary;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const ranked = layout.nodes
    .filter((n) => (n.node.time_ms ?? 0) > 0)
    .sort((a, b) => (b.node.time_ms ?? 0) - (a.node.time_ms ?? 0))
    .slice(0, 5);
  if (ranked.length === 0) return null;
  return (
    <Section title="Most expensive operators">
      <div className="flex flex-col gap-1">
        {ranked.map((gn) => {
          const pct =
            summary.latency_ms > 0
              ? ((gn.node.time_ms ?? 0) / summary.latency_ms) * 100
              : 0;
          return (
            <button
              key={gn.id}
              type="button"
              onClick={() => onSelect(gn.id)}
              className={cn(
                "flex items-center gap-2 rounded px-1.5 py-1 text-left hover:bg-accent/50",
                gn.id === selectedId && "bg-accent/60",
              )}
            >
              <span className="min-w-0 flex-1 truncate font-mono text-2xs text-text-primary">
                {gn.node.type}
              </span>
              <div className="h-1 w-12 shrink-0 overflow-hidden rounded bg-[var(--bg-elevated)]">
                <div
                  className="h-full rounded bg-[var(--brand-yellow)]"
                  style={{ width: `${Math.min(100, pct)}%` }}
                />
              </div>
              <span className="w-12 shrink-0 text-right font-mono text-2xs text-text-tertiary font-tabular">
                {fmtMs(gn.node.time_ms)}
              </span>
            </button>
          );
        })}
      </div>
    </Section>
  );
}

function Diagnostics({
  layout,
  summary,
  onSelect,
}: {
  layout: GraphLayout;
  summary: QueryProfileSummary;
  onSelect: (id: string) => void;
}) {
  const issues: { id: string | null; label: string; detail: string }[] = [];
  if (isSpilled(summary)) {
    const reserved = summary.reserved_memory_bytes;
    const detail =
      reserved != null
        ? `${formatBytes(summary.spill_bytes)} spilled over a ${formatBytes(reserved)} reservation — give it more memory (e.g. SET duckhaven_concurrency='single').`
        : `${formatBytes(summary.spill_bytes)} spilled — consider a larger reservation.`;
    issues.push({ id: null, label: BADGE_LABELS.spill, detail });
  }
  for (const gn of layout.nodes) {
    for (const b of nodeBadges(gn.node, summary) as NodeBadge[]) {
      issues.push({ id: gn.id, label: BADGE_LABELS[b], detail: gn.node.type });
    }
  }
  return (
    <Section title="Diagnostics">
      {issues.length === 0 ? (
        <p className="text-xs text-text-tertiary">
          No inefficiencies detected.
        </p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {issues.map((issue, i) => (
            <button
              key={i}
              type="button"
              disabled={issue.id === null}
              onClick={() => issue.id && onSelect(issue.id)}
              className={cn(
                "flex flex-col gap-0.5 rounded border border-[var(--border-subtle)] px-2 py-1 text-left",
                issue.id !== null && "hover:border-[var(--border-strong)]",
              )}
            >
              <span className="text-2xs font-medium text-[var(--status-failed)]">
                {issue.label}
              </span>
              <span className="font-mono text-2xs text-text-secondary">
                {issue.detail}
              </span>
            </button>
          ))}
        </div>
      )}
    </Section>
  );
}

export function ProfileSidebar({
  layout,
  summary,
  selectedId,
  onSelect,
}: {
  layout: GraphLayout;
  summary: QueryProfileSummary;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const selected =
    layout.nodes.find((n) => n.id === selectedId) ?? layout.nodes[0];
  return (
    <aside className="flex w-80 shrink-0 flex-col gap-5 overflow-auto border-l border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
      {selected && <NodeDetail node={selected.node} summary={summary} />}
      <MostExpensive
        layout={layout}
        summary={summary}
        selectedId={selectedId}
        onSelect={onSelect}
      />
      <Diagnostics layout={layout} summary={summary} onSelect={onSelect} />
    </aside>
  );
}
