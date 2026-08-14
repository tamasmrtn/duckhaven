import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { GitBranch, TriangleAlert } from "lucide-react";
import { Banner } from "@/components/ui/banner";
import { EmptyState } from "@/components/app/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { useTableLineage } from "@/queries/lineage";
import type {
  LineageDirection,
  LineageEdge,
  LineageNode,
} from "@/types/lineage";
import { LineageGraph } from "./LineageGraph";
import { layoutLineage } from "./layout";

const DIRECTIONS: { value: LineageDirection; label: string }[] = [
  { value: "upstream", label: "Upstream" },
  { value: "both", label: "Both" },
  { value: "downstream", label: "Downstream" },
];

const DEPTHS = [1, 2, 3];

// Direction-aware, because one phrasing cannot serve both sides of an edge:
// read from the target, `create_table_as` means "created from X"; read from the
// source it means "used to create X". A single map plus a "(downstream)" suffix
// states the relationship backwards for every outgoing edge.
const INCOMING_LABEL: Record<string, string> = {
  create_table_as: "Created from",
  create_view: "View over",
  insert: "Inserted from",
  update: "Updated from",
  merge: "Merged from",
  delete: "Deleted using",
  model: "Declared dependency on",
};

const OUTGOING_LABEL: Record<string, string> = {
  create_table_as: "Used to create",
  create_view: "Backs the view",
  insert: "Inserted into",
  update: "Used to update",
  merge: "Merged into",
  delete: "Used to delete from",
  model: "Declared dependency of",
};

function edgeLabel(operation: string | null, isIncoming: boolean): string {
  const map = isIncoming ? INCOMING_LABEL : OUTGOING_LABEL;
  if (!operation) return isIncoming ? "Built using" : "Used to build";
  return map[operation] ?? operation;
}

function Segmented<T extends string | number>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-2xs uppercase tracking-wide text-text-tertiary">
        {label}
      </span>
      <div
        role="group"
        aria-label={label}
        className="flex rounded-md border border-[var(--border-subtle)] p-0.5"
      >
        {options.map((option) => (
          <button
            key={String(option.value)}
            type="button"
            aria-pressed={option.value === value}
            onClick={() => onChange(option.value)}
            className={
              option.value === value
                ? "rounded px-2 py-0.5 text-2xs bg-accent text-text-primary"
                : "rounded px-2 py-0.5 text-2xs text-text-secondary hover:text-text-primary"
            }
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function nodeName(node: LineageNode | undefined): string {
  if (!node) return "?";
  if (node.kind === "redacted") return "a restricted table";
  if (node.kind === "external") return `${node.system}.${node.table}`;
  return `${node.schema_name}.${node.table}`;
}

/**
 * Every relationship touching the selected node, not just one of them. A node
 * commonly sits on several edges with different providers behind each, so
 * picking one arbitrarily would misreport where the data came from.
 */
function EdgeDetails({
  ws,
  edges,
  selected,
  nodesByKey,
}: {
  ws: string;
  edges: LineageEdge[];
  selected: string;
  nodesByKey: Map<string, LineageNode>;
}) {
  return (
    <div className="flex max-h-40 flex-col gap-3 overflow-y-auto border-t border-[var(--border-subtle)] p-3 text-xs">
      {edges.map((edge) => {
        const isIncoming = edge.target_key === selected;
        const other = nodesByKey.get(
          isIncoming ? edge.source_key : edge.target_key,
        );
        return (
          <div
            key={`${edge.source_key}->${edge.target_key}`}
            className="flex flex-col gap-1"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="min-w-0 truncate text-text-secondary">
                {edgeLabel(edge.operation, isIncoming)}{" "}
                <span className="font-mono text-text-primary">
                  {nodeName(other)}
                </span>
              </span>
              <span className="shrink-0 font-mono text-2xs text-text-tertiary">
                seen {edge.observation_count}×
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-1">
              {edge.providers.map((provider) => (
                <span
                  key={provider}
                  className="rounded border border-[var(--border-subtle)] px-1.5 py-0.5 font-mono text-2xs text-text-secondary"
                >
                  {provider}
                </span>
              ))}
              <span className="text-2xs text-text-tertiary">
                last seen {new Date(edge.last_seen_at).toLocaleDateString()}
              </span>
              {edge.last_query_id && (
                <Link
                  to="/$ws/queries/$queryId"
                  params={{ ws, queryId: edge.last_query_id }}
                  className="text-2xs text-[var(--brand-maya-blue)] hover:underline"
                >
                  view query ↗
                </Link>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * The Lineage tab of the table detail view. Follows the same contract as the
 * sibling panels (SnapshotHistoryPanel, TableHealthPanel, PermissionsPanel):
 * everything it needs is the table's address.
 *
 * Depth defaults to 2 rather than the maximum — a lineage graph is exactly the
 * kind of thing that looks small until one hub table makes it enormous, so the
 * cheap view is the one you get without asking.
 */
export function LineagePanel({
  ws,
  catalog,
  schema,
  table,
}: {
  ws: string;
  catalog: string;
  schema: string;
  table: string;
}) {
  const [direction, setDirection] = useState<LineageDirection>("both");
  const [depth, setDepth] = useState(2);
  const [selected, setSelected] = useState<string | null>(null);

  const { data, isLoading, error } = useTableLineage(
    ws,
    catalog,
    schema,
    table,
    direction,
    depth,
  );

  if (isLoading) {
    return (
      <div className="flex flex-col gap-2 p-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (error) {
    return (
      <EmptyState
        icon={TriangleAlert}
        title="Could not load lineage"
        description={error instanceof Error ? error.message : undefined}
      />
    );
  }

  const graph = data ?? {
    root: "",
    nodes: [],
    edges: [],
    truncated: false,
  };
  const layout = layoutLineage(graph.nodes, graph.edges);
  const hasLineage = graph.edges.length > 0;
  const nodesByKey = new Map(graph.nodes.map((n) => [n.key, n]));
  const selectedEdges = selected
    ? graph.edges.filter(
        (e) => e.source_key === selected || e.target_key === selected,
      )
    : [];

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-4 px-4 py-2 shrink-0">
        <Segmented
          label="Direction"
          options={DIRECTIONS}
          value={direction}
          onChange={setDirection}
        />
        <Segmented
          label="Depth"
          options={DEPTHS.map((d) => ({ value: d, label: String(d) }))}
          value={depth}
          onChange={setDepth}
        />
      </div>

      {graph.truncated && (
        <Banner className="mx-4 mb-2">
          <TriangleAlert className="size-3.5 shrink-0" />
          This graph is larger than we render at once — some nodes are not
          shown. Narrow the direction or reduce the depth.
        </Banner>
      )}

      {hasLineage ? (
        <>
          <div className="min-h-0 flex-1">
            <LineageGraph
              layout={layout}
              rootKey={graph.root}
              selectedId={selected}
              onSelect={setSelected}
            />
          </div>
          {selected && selectedEdges.length > 0 && (
            <EdgeDetails
              ws={ws}
              edges={selectedEdges}
              selected={selected}
              nodesByKey={nodesByKey}
            />
          )}
        </>
      ) : (
        <EmptyState
          icon={GitBranch}
          title={
            direction === "both"
              ? "No lineage recorded for this table yet"
              : `No ${direction} lineage for this table`
          }
          description={
            direction === "both"
              ? "Lineage appears once a query builds this table from another one, or once you import it from the tool that does."
              : "Nothing in this direction. The other direction may still have lineage — try the Direction toggle above."
          }
        />
      )}
    </div>
  );
}
