import { useRef, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  EyeOff,
  Minus,
  Plus,
  Table2,
  ExternalLink,
} from "lucide-react";
import { cn } from "@/utils";
import type { LineageNode } from "@/types/lineage";
import {
  NODE_HEIGHT,
  NODE_WIDTH,
  ROW_HEIGHT,
  type LineageGraphLayout,
} from "./layout";

const ZOOM_MIN = 0.25;
const ZOOM_MAX = 2;
const ZOOM_STEP = 0.1;

function nodeLabel(node: LineageNode): string {
  if (node.kind === "redacted") return "Restricted";
  return node.table ?? "—";
}

function nodeSublabel(node: LineageNode): string {
  if (node.kind === "redacted") return "no access";
  if (node.kind === "external") return `${node.system} · ${node.schema_name}`;
  return `${node.catalog}.${node.schema_name}`;
}

/**
 * The lineage canvas: upstream on the left, the selected table in the middle,
 * downstream on the right. Reuses the profile graph's rendering approach —
 * absolutely-positioned node buttons over an SVG edge layer, click-drag panning
 * and a zoom control — so the two graphs in the product feel like one thing.
 *
 * A node can be expanded to show which of its columns take part in the lineage
 * around it. Collapsed is the default and stays the whole story for anyone who
 * only wants the table graph: nothing about column detail is fetched, drawn or
 * paid for until somebody opens a node.
 */
export function LineageGraph({
  layout,
  rootKey,
  selectedId,
  onSelect,
  expanded,
  onToggleExpanded,
  canExpand,
  selectedColumn,
  onSelectColumn,
}: {
  layout: LineageGraphLayout;
  rootKey: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
  expanded: ReadonlySet<string>;
  onToggleExpanded: (id: string) => void;
  /** Whether this node has any column detail to show. */
  canExpand: (id: string) => boolean;
  selectedColumn: { key: string; column: string } | null;
  onSelectColumn: (key: string, column: string) => void;
}) {
  const byId = new Map(layout.nodes.map((n) => [n.id, n]));
  const [zoom, setZoom] = useState(1);
  const [isPanning, setIsPanning] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

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
        data-testid="lineage-graph-scroll"
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
                  id="dh-lineage-arrow"
                  markerWidth="8"
                  markerHeight="8"
                  refX="6"
                  refY="4"
                  orient="auto"
                >
                  <path
                    d="M0,0 L8,4 L0,8"
                    fill="none"
                    stroke="var(--border-strong)"
                    strokeWidth="1.2"
                  />
                </marker>
              </defs>
              {layout.edges.map((e) => {
                const from = byId.get(e.from);
                const to = byId.get(e.to);
                if (!from || !to) return null;
                const x1 = from.x + NODE_WIDTH / 2;
                const x2 = to.x - NODE_WIDTH / 2;
                // Anchored on the header rather than the box's middle, so an
                // expanded node's table-level line still leaves from the table
                // and not from the middle of its column list.
                const y1 = from.y + NODE_HEIGHT / 2;
                const y2 = to.y + NODE_HEIGHT / 2;
                const midX = (x1 + x2) / 2;
                // A relationship no producer has re-asserted lately is drawn
                // dashed and faint: still there, still navigable, but visibly
                // unconfirmed. Removing it would be the worse lie of the two.
                const stale = e.edge.stale;
                // Once both ends are open the column links say everything the
                // table line did and more, so the table line steps back rather
                // than competing with them.
                const detailed = e.columnLinks.length > 0;
                return (
                  <g key={`${e.from}->${e.to}`}>
                    <path
                      d={`M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`}
                      fill="none"
                      stroke="var(--border-strong)"
                      strokeWidth="1.2"
                      strokeDasharray={stale ? "4 3" : undefined}
                      opacity={detailed ? 0.25 : stale ? 0.5 : undefined}
                      markerEnd={
                        detailed ? undefined : "url(#dh-lineage-arrow)"
                      }
                    />
                    {e.columnLinks.map((link) => {
                      const lit =
                        selectedColumn !== null &&
                        ((selectedColumn.key === e.from &&
                          selectedColumn.column ===
                            link.column.source_column) ||
                          (selectedColumn.key === e.to &&
                            selectedColumn.column ===
                              link.column.target_column));
                      return (
                        <path
                          key={`${link.column.source_column}->${link.column.target_column}`}
                          data-testid="lineage-column-link"
                          d={`M ${x1} ${link.fromY} C ${midX} ${link.fromY}, ${midX} ${link.toY}, ${x2} ${link.toY}`}
                          fill="none"
                          stroke={
                            lit
                              ? "var(--brand-yellow)"
                              : "var(--brand-maya-blue)"
                          }
                          strokeWidth={lit ? 1.8 : 1}
                          strokeDasharray={
                            link.column.stale ? "4 3" : undefined
                          }
                          opacity={
                            selectedColumn && !lit
                              ? 0.2
                              : link.column.stale
                                ? 0.5
                                : 0.75
                          }
                          markerEnd="url(#dh-lineage-arrow)"
                        />
                      );
                    })}
                  </g>
                );
              })}
            </svg>

            {layout.nodes.map((gn) => {
              const selected = gn.id === selectedId;
              const isRoot = gn.id === rootKey;
              const isOpen = expanded.has(gn.id);
              const expandable = canExpand(gn.id);
              const Icon =
                gn.node.kind === "redacted"
                  ? EyeOff
                  : gn.node.kind === "external"
                    ? ExternalLink
                    : Table2;
              return (
                <div
                  key={gn.id}
                  className={cn(
                    "absolute flex flex-col rounded-md border text-left shadow-e1 transition-colors",
                    gn.node.kind === "redacted"
                      ? "border-dashed bg-[var(--bg-elevated)]"
                      : "bg-[var(--bg-surface)]",
                    selected
                      ? "border-[var(--brand-yellow)] ring-2 ring-[var(--brand-yellow)]"
                      : isRoot
                        ? "border-[var(--brand-maya-blue)]"
                        : "border-[var(--border-subtle)] hover:border-[var(--border-strong)]",
                  )}
                  style={{
                    width: NODE_WIDTH,
                    height: gn.height,
                    left: gn.x - NODE_WIDTH / 2,
                    top: gn.y,
                  }}
                >
                  <button
                    type="button"
                    onClick={() => onSelect(gn.id)}
                    aria-pressed={selected}
                    className="flex cursor-pointer flex-col gap-0.5 px-2.5 py-1.5 text-left"
                    style={{ height: NODE_HEIGHT }}
                  >
                    <div className="flex items-center gap-1.5">
                      <Icon className="size-3 shrink-0 text-text-tertiary" />
                      <span className="min-w-0 flex-1 truncate font-mono text-xs font-medium text-text-primary">
                        {nodeLabel(gn.node)}
                      </span>
                    </div>
                    <span className="truncate font-mono text-2xs text-text-tertiary">
                      {nodeSublabel(gn.node)}
                    </span>
                  </button>

                  {/* Absent, not disabled, when there is nothing to open: an
                      affordance that never does anything is worse than none. */}
                  {expandable && (
                    <button
                      type="button"
                      aria-expanded={isOpen}
                      aria-label={`${isOpen ? "Hide" : "Show"} columns for ${nodeLabel(gn.node)}`}
                      onClick={() => onToggleExpanded(gn.id)}
                      className="absolute right-1 top-1 rounded p-0.5 text-text-tertiary hover:bg-accent hover:text-text-primary"
                    >
                      {isOpen ? (
                        <ChevronDown className="size-3" />
                      ) : (
                        <ChevronRight className="size-3" />
                      )}
                    </button>
                  )}

                  {gn.rows.length > 0 && (
                    <ul
                      className="flex flex-col border-t border-[var(--border-subtle)] py-1.5"
                      aria-label={`Columns of ${nodeLabel(gn.node)}`}
                    >
                      {gn.rows.map((row) => {
                        const lit =
                          selectedColumn?.key === gn.id &&
                          selectedColumn.column === row.column;
                        return (
                          <li key={row.column}>
                            <button
                              type="button"
                              aria-pressed={lit}
                              onClick={() => onSelectColumn(gn.id, row.column)}
                              style={{ height: ROW_HEIGHT }}
                              className={cn(
                                "flex w-full items-center truncate px-2.5 text-left font-mono text-2xs",
                                lit
                                  ? "bg-[var(--brand-yellow)]/15 text-text-primary"
                                  : "text-text-secondary hover:bg-accent",
                              )}
                            >
                              {row.column}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
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
