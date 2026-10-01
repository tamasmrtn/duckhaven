import { useMemo, useState } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { Skeleton } from "@/components/ui/skeleton";
import { useIsDark } from "@/hooks/useIsDark";
import { useAgentMonitoring } from "@/queries/agents";
import type { Agent, AgentQuerySort, MonitoringWindow } from "@/types/agent";
import { cn } from "@/utils";
import { AgentQueriesTable, type TableScope } from "./AgentQueriesTable";
import { LiveTiles, agentSize } from "./LiveTiles";
import {
  ConcurrencyPanel,
  CpuPanel,
  MemoryPanel,
  QueriesPanel,
  TimelinePanel,
  toRows,
  type PanelContext,
} from "./MonitoringCharts";
import { RangeControl, bucketLabel } from "./RangeControl";
import { rangeFromSearch } from "./range";
import { SummaryKpis } from "./SummaryKpis";
import { release, selection, useZoom } from "./useZoom";

// A range whose end is this close to "now" is live: its last bucket is still
// filling in, and is drawn as such.
const LIVE_SLACK_MS = 2 * 60_000;

function indexOf(state: { activeTooltipIndex?: unknown }): number | null {
  const raw = state.activeTooltipIndex;
  if (raw == null || raw === "") return null;
  const i = Number(raw);
  return Number.isFinite(i) ? i : null;
}

/**
 * The Monitoring tab, read top to bottom: what the agent is doing now, what the
 * range added up to, then every series on one time axis — drag across any of them
 * to zoom, click a bar to list the queries in it — and finally those queries,
 * sortable by what they cost.
 */
export function MonitoringTab({ ws, agent }: { ws: string; agent: Agent }) {
  const search = useSearch({ from: "/$ws/compute/$agentId" });
  const navigate = useNavigate();
  const range = rangeFromSearch(search);
  const { data, isLoading, isFetching } = useAgentMonitoring(agent.id, range);
  const dark = useIsDark();
  const [zoom, dispatch] = useZoom();
  const [selected, setSelected] = useState<number | null>(null);
  const [sort, setSort] = useState<AgentQuerySort>("started_at");

  function setSearch(next: {
    range?: MonitoringWindow;
    from?: string;
    to?: string;
  }) {
    setSelected(null);
    void navigate({
      to: "/$ws/compute/$agentId",
      params: { ws, agentId: agent.id },
      search: next,
    });
  }

  const ctx = useMemo<PanelContext | null>(() => {
    if (!data) return null;
    const { rows, stepRows } = toRows(data);
    const startMs = Date.parse(data.range_start);
    const endMs = Date.parse(data.range_end);
    const generated = Date.parse(data.generated_at);
    const last = rows[rows.length - 1];
    const bucketAt = (i: number) =>
      rows[Math.max(0, Math.min(rows.length - 1, i))];
    const pick = selected != null ? rows[selected] : undefined;
    const { cores, memoryGb } = agentSize(agent);

    return {
      data,
      rows,
      stepRows,
      startMs,
      endMs,
      downSpans: data.spans
        .filter((s) => s.state === "down")
        .map(
          (s) => [Date.parse(s.start), Date.parse(s.end)] as [number, number],
        ),
      live:
        last && last.partial && generated - endMs < LIVE_SLACK_MS
          ? [last.x, last.end]
          : null,
      selected: pick ? [pick.x, pick.end] : null,
      dragging: selection(zoom),
      bucketLabel: bucketLabel(data.bucket_seconds),
      cores,
      memoryGb,
      dark,
      handlers: {
        onMouseDown: (state) => {
          const i = indexOf(state);
          if (i != null && rows.length)
            dispatch({ type: "down", at: bucketAt(i).x });
        },
        onMouseMove: (state) => {
          const i = indexOf(state);
          if (i == null || zoom.anchor == null || !rows.length) return;
          const b = bucketAt(i);
          dispatch({ type: "move", at: b.x >= zoom.anchor ? b.end : b.x });
        },
        onMouseUp: () => {
          const outcome = release(zoom, null);
          dispatch({ type: "cancel" });
          if (outcome.kind === "zoom") {
            setSearch({
              from: new Date(outcome.from).toISOString(),
              to: new Date(outcome.to).toISOString(),
            });
          } else if (outcome.kind === "select") {
            const i = rows.findIndex((r) => r.x === outcome.at);
            setSelected(i === selected ? null : i);
          }
        },
        onMouseLeave: () => dispatch({ type: "cancel" }),
      },
    };
    // setSearch is recreated each render but only reads stable values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, zoom, selected, dark, agent]);

  const scope: TableScope | null = ctx
    ? ctx.selected
      ? {
          start: new Date(ctx.selected[0]).toISOString(),
          end: new Date(ctx.selected[1]).toISOString(),
          bucket: `${new Date(ctx.selected[0]).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })}–${new Date(ctx.selected[1]).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })}`,
        }
      : { start: ctx.data.range_start, end: ctx.data.range_end }
    : null;

  return (
    <div className="space-y-4">
      <LiveTiles agent={agent} />

      <RangeControl
        window={"window" in range ? range.window : null}
        data={data}
        onWindow={(w) => setSearch({ range: w })}
        onReset={() => setSearch({})}
      />

      {isLoading || !ctx ? (
        <div className="space-y-4">
          <Skeleton className="h-16 w-full rounded-lg" />
          <Skeleton className="h-56 w-full rounded-lg" />
          <Skeleton className="h-56 w-full rounded-lg" />
        </div>
      ) : (
        // Dim rather than unmount while the next range loads, so changing it
        // never collapses the page back to skeletons.
        <div
          className={cn(
            "space-y-4 transition-opacity",
            isFetching && "opacity-60",
          )}
        >
          <SummaryKpis summary={ctx.data.summary} />
          <div className="select-none space-y-4">
            <TimelinePanel ctx={ctx} />
            <QueriesPanel ctx={ctx} />
            <ConcurrencyPanel ctx={ctx} />
            <CpuPanel ctx={ctx} />
            <MemoryPanel ctx={ctx} />
          </div>
          {scope && (
            <AgentQueriesTable
              ws={ws}
              agentId={agent.id}
              scope={scope}
              sort={sort}
              onSort={setSort}
              onClearBucket={() => setSelected(null)}
              finished={ctx.data.summary.finished}
            />
          )}
        </div>
      )}
    </div>
  );
}
