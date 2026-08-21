import { useRef, useState } from "react";
import { Minus, Plus } from "lucide-react";
import type { QueryProfileNode, QueryProfileSummary } from "@/types/query";
import { cn } from "@/utils";
import {
  BADGE_LABELS,
  type NodeBadge,
  ROWS_READ_HINT,
  isRowsReadCorrected,
  nodeBadges,
  rowsReadByScan,
} from "@/features/worksheet/profile/highlights";
import { NODE_HEIGHT, NODE_WIDTH, type GraphLayout } from "./layout";

const BADGE_DOT: Record<NodeBadge, string> = {
  scan: "bg-[var(--status-failed)]",
  estimate: "bg-[var(--brand-orange)]",
  time: "bg-[var(--brand-yellow)]",
};

const ZOOM_MIN = 0.25;
const ZOOM_MAX = 2;
const ZOOM_STEP = 0.1;

function fmtMs(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 1 : 0)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function rowsLabel(
  node: QueryProfileNode,
  summary: QueryProfileSummary,
): string {
  const fmt = (n: number | null) => (n == null ? "—" : n.toLocaleString());
  // Rows read, corrected for DuckDB's per-thread double count — the raw figure
  // counts the whole relation once per participating thread.
  if (node.rows_scanned && node.rows_scanned > 0) {
    return `${rowsReadByScan(node, summary).toLocaleString()} → ${fmt(node.rows_produced)}`;
  }
  return `${fmt(node.rows_produced)} rows`;
}

/**
 * The clickable operator graph. Result/root on top; edges run down to children
 * with the arrowhead pointing up (data flows scans → result). Selecting a node
 * lifts the choice to the parent so the side panel can show its detail.
 */
export function ProfileGraph({
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
  const byId = new Map(layout.nodes.map((n) => [n.id, n]));
  const [zoom, setZoom] = useState(1);
  const [isPanning, setIsPanning] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Click-drag to pan, the usual plan-viewer interaction. A press that
  // starts on a node button is left alone so selecting a node still works;
  // everything else grabs the scroll container and drags it under the cursor.
  function handleMouseDown(e: React.MouseEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    if ((e.target as HTMLElement).closest("button")) return;
    const el = containerRef.current;
    if (!el) return;
    e.preventDefault();

    const startX = e.clientX;
    const startY = e.clientY;
    const startScrollLeft = el.scrollLeft;
    const startScrollTop = el.scrollTop;
    setIsPanning(true);

    function onMove(ev: MouseEvent) {
      el!.scrollLeft = startScrollLeft - (ev.clientX - startX);
      el!.scrollTop = startScrollTop - (ev.clientY - startY);
    }
    function onUp() {
      setIsPanning(false);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  return (
    <div className="relative h-full w-full">
      <div
        ref={containerRef}
        data-testid="profile-graph-scroll"
        onMouseDown={handleMouseDown}
        className={cn(
          "h-full w-full overflow-auto p-6",
          isPanning ? "cursor-grabbing select-none" : "cursor-grab",
        )}
      >
        <div
          className="relative mx-auto"
          style={{ width: layout.width * zoom, height: layout.height * zoom }}
        >
          <div
            className="absolute left-0 top-0"
            style={{
              width: layout.width,
              height: layout.height,
              transform: `scale(${zoom})`,
              transformOrigin: "top left",
            }}
          >
            <svg
              className="pointer-events-none absolute inset-0"
              width={layout.width}
              height={layout.height}
            >
              <defs>
                <marker
                  id="dh-flow-arrow"
                  markerWidth="8"
                  markerHeight="8"
                  refX="4"
                  refY="2"
                  orient="auto"
                >
                  <path
                    d="M0,4 L4,0 L8,4"
                    fill="none"
                    stroke="var(--border-strong)"
                    strokeWidth="1.2"
                  />
                </marker>
              </defs>
              {layout.edges.map((e) => {
                const parent = byId.get(e.from);
                const child = byId.get(e.to);
                if (!parent || !child) return null;
                const x1 = child.x;
                const y1 = child.y; // child top
                const x2 = parent.x;
                const y2 = parent.y + NODE_HEIGHT; // parent bottom
                const midY = (y1 + y2) / 2;
                // Arrowhead points up toward the parent (data flow direction).
                return (
                  <path
                    key={`${e.from}->${e.to}`}
                    d={`M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`}
                    fill="none"
                    stroke="var(--border-strong)"
                    strokeWidth="1.2"
                    markerEnd="url(#dh-flow-arrow)"
                  />
                );
              })}
            </svg>

            {layout.nodes.map((gn) => {
              const badges = nodeBadges(gn.node, summary);
              const timePct =
                summary.latency_ms > 0 && gn.node.time_ms != null
                  ? Math.min(100, (gn.node.time_ms / summary.latency_ms) * 100)
                  : 0;
              const selected = gn.id === selectedId;
              return (
                <button
                  key={gn.id}
                  type="button"
                  onClick={() => onSelect(gn.id)}
                  aria-pressed={selected}
                  className={cn(
                    "absolute flex cursor-pointer flex-col gap-1 rounded-md border bg-[var(--bg-surface)] px-2.5 py-1.5 text-left shadow-e1 transition-colors",
                    selected
                      ? "border-[var(--brand-yellow)] ring-2 ring-[var(--brand-yellow)]"
                      : "border-[var(--border-subtle)] hover:border-[var(--border-strong)]",
                  )}
                  style={{
                    width: NODE_WIDTH,
                    height: NODE_HEIGHT,
                    left: gn.x - NODE_WIDTH / 2,
                    top: gn.y,
                  }}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="min-w-0 flex-1 truncate font-mono text-xs font-medium text-text-primary">
                      {gn.node.name || gn.node.type}
                    </span>
                    {badges.map((b) => (
                      <span
                        key={b}
                        className={cn(
                          "size-1.5 shrink-0 rounded-full",
                          BADGE_DOT[b],
                        )}
                        title={BADGE_LABELS[b]}
                      />
                    ))}
                  </div>
                  <div className="flex items-center gap-1.5">
                    <div className="h-1 flex-1 overflow-hidden rounded bg-[var(--bg-elevated)]">
                      <div
                        className="h-full rounded bg-[var(--brand-yellow)]"
                        style={{ width: `${timePct}%` }}
                      />
                    </div>
                    <span className="shrink-0 font-mono text-2xs text-text-tertiary font-tabular">
                      {fmtMs(gn.node.time_ms)}
                    </span>
                  </div>
                  <span
                    className="truncate font-mono text-2xs text-text-tertiary font-tabular"
                    title={
                      gn.node.rows_scanned && isRowsReadCorrected(summary)
                        ? ROWS_READ_HINT
                        : undefined
                    }
                  >
                    {rowsLabel(gn.node, summary)}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="absolute bottom-4 right-4 flex items-center gap-0.5 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-1 shadow-e1">
        <button
          type="button"
          aria-label="Zoom out"
          disabled={zoom <= ZOOM_MIN}
          onClick={() =>
            setZoom((z) => Math.max(ZOOM_MIN, +(z - ZOOM_STEP).toFixed(2)))
          }
          className="rounded p-1 text-text-secondary hover:bg-accent hover:text-text-primary disabled:pointer-events-none disabled:opacity-40"
        >
          <Minus className="size-3.5" />
        </button>
        <button
          type="button"
          aria-label="Reset zoom"
          onClick={() => setZoom(1)}
          className="min-w-10 rounded px-1 py-1 text-center font-mono text-2xs text-text-secondary hover:bg-accent hover:text-text-primary"
        >
          {Math.round(zoom * 100)}%
        </button>
        <button
          type="button"
          aria-label="Zoom in"
          disabled={zoom >= ZOOM_MAX}
          onClick={() =>
            setZoom((z) => Math.min(ZOOM_MAX, +(z + ZOOM_STEP).toFixed(2)))
          }
          className="rounded p-1 text-text-secondary hover:bg-accent hover:text-text-primary disabled:pointer-events-none disabled:opacity-40"
        >
          <Plus className="size-3.5" />
        </button>
      </div>
    </div>
  );
}
