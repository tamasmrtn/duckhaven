import { useRef, useState } from "react";
import { EyeOff, Minus, Plus, Table2, ExternalLink } from "lucide-react";
import { cn } from "@/utils";
import type { LineageNode } from "@/types/lineage";
import { NODE_HEIGHT, NODE_WIDTH, type LineageGraphLayout } from "./layout";

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
 */
export function LineageGraph({
  layout,
  rootKey,
  selectedId,
  onSelect,
}: {
  layout: LineageGraphLayout;
  rootKey: string;
  selectedId: string | null;
  onSelect: (id: string) => void;
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
                const y1 = from.y + NODE_HEIGHT / 2;
                const x2 = to.x - NODE_WIDTH / 2;
                const y2 = to.y + NODE_HEIGHT / 2;
                const midX = (x1 + x2) / 2;
                // A relationship no producer has re-asserted lately is drawn
                // dashed and faint: still there, still navigable, but visibly
                // unconfirmed. Removing it would be the worse lie of the two.
                const stale = e.edge.stale;
                return (
                  <path
                    key={`${e.from}->${e.to}`}
                    d={`M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`}
                    fill="none"
                    stroke="var(--border-strong)"
                    strokeWidth="1.2"
                    strokeDasharray={stale ? "4 3" : undefined}
                    opacity={stale ? 0.5 : undefined}
                    markerEnd="url(#dh-lineage-arrow)"
                  />
                );
              })}
            </svg>

            {layout.nodes.map((gn) => {
              const selected = gn.id === selectedId;
              const isRoot = gn.id === rootKey;
              const Icon =
                gn.node.kind === "redacted"
                  ? EyeOff
                  : gn.node.kind === "external"
                    ? ExternalLink
                    : Table2;
              return (
                <button
                  key={gn.id}
                  type="button"
                  onClick={() => onSelect(gn.id)}
                  aria-pressed={selected}
                  className={cn(
                    "absolute flex cursor-pointer flex-col gap-0.5 rounded-md border px-2.5 py-1.5 text-left shadow-e1 transition-colors",
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
                    height: NODE_HEIGHT,
                    left: gn.x - NODE_WIDTH / 2,
                    top: gn.y,
                  }}
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
