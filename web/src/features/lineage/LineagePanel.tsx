import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { EyeOff, GitBranch, TriangleAlert } from "lucide-react";
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
/**
 * What an edge can say about its columns, in words rather than a state name.
 *
 * The three cases are genuinely different answers and must not collapse into
 * one. "No columns flow" is a finding — the source was read and none of its
 * values reached the target, which is precisely what the table graph could never
 * tell you. "Not available" is an absence of knowledge. Showing an empty list for
 * both would turn the second into the first.
 */
function ColumnDetails({ edge }: { edge: LineageEdge }) {
  if (edge.column_lineage !== "derived") {
    return (
      <p className="text-2xs text-text-tertiary">
        Column detail is not available for this relationship.
      </p>
    );
  }
  if (edge.columns.length === 0) {
    return (
      <p className="text-2xs text-text-tertiary">
        No columns flow along this relationship — the source was read but none
        of its values reached the target.
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-0.5">
      {edge.columns.map((column) => (
        <li
          key={`${column.source_column}->${column.target_column}`}
          className="flex items-center gap-1.5 font-mono text-2xs"
        >
          <span className="text-text-secondary">{column.source_column}</span>
          <span className="text-text-tertiary">→</span>
          <span className="text-text-primary">{column.target_column}</span>
          {column.providers.map((name) => (
            <span
              key={name}
              className="rounded border border-[var(--border-subtle)] px-1 text-2xs text-text-tertiary"
            >
              {name}
            </span>
          ))}
          {column.stale && (
            <span className="text-2xs text-text-tertiary">· stale</span>
          )}
        </li>
      ))}
    </ul>
  );
}

function EdgeDetails({
  ws,
  edges,
  selected,
  nodesByKey,
  showColumns,
}: {
  ws: string;
  edges: LineageEdge[];
  selected: string;
  nodesByKey: Map<string, LineageNode>;
  /** Only once a node is open — otherwise nothing has been fetched to show. */
  showColumns: boolean;
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
              {/* Each producer carries its own last-seen date, because that is
                  the whole point: one that stopped reporting should be visible
                  as such even while another keeps confirming the same pair. */}
              {edge.providers.map((provider) => (
                <span
                  key={provider.name}
                  title={`${provider.name} last confirmed this on ${new Date(
                    provider.last_seen_at,
                  ).toLocaleDateString()} (seen ${provider.observation_count}×)`}
                  className={
                    provider.stale
                      ? "rounded border border-dashed border-[var(--border-subtle)] px-1.5 py-0.5 font-mono text-2xs text-text-tertiary"
                      : "rounded border border-[var(--border-subtle)] px-1.5 py-0.5 font-mono text-2xs text-text-secondary"
                  }
                >
                  {provider.name}
                  {provider.stale && " · stale"}
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
            {showColumns && <ColumnDetails edge={edge} />}
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
  // Nodes whose columns the user has opened. Empty by default, and the request
  // asks for no column detail at all while it stays that way — so anyone who
  // only wants the table graph pays nothing for the fact that this exists.
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [selectedColumn, setSelectedColumn] = useState<{
    key: string;
    column: string;
  } | null>(null);

  const { data, isLoading, error } = useTableLineage(
    ws,
    catalog,
    schema,
    table,
    direction,
    depth,
    true,
    [...expanded],
  );

  function toggleExpanded(key: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    // A column highlighted inside a node that is being closed has nowhere left
    // to show itself.
    setSelectedColumn((current) => (current?.key === key ? null : current));
  }

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
    hidden: false,
    columns_truncated: false,
  };
  const layout = layoutLineage(graph.nodes, graph.edges, expanded);
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

      {graph.columns_truncated && (
        <Banner className="mx-4 mb-2">
          <TriangleAlert className="size-3.5 shrink-0" />
          There is more column detail here than we show at once — some column
          relationships are not listed. Collapse a table to narrow it down.
        </Banner>
      )}

      {/* Deliberately says nothing about what is missing. The point is only that
          "nothing here" would be the wrong conclusion to draw. */}
      {graph.hidden && hasLineage && (
        <Banner className="mx-4 mb-2">
          <EyeOff className="size-3.5 shrink-0" />
          Part of this graph is outside this workspace and is not shown.
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
              expanded={expanded}
              onToggleExpanded={toggleExpanded}
              selectedColumn={selectedColumn}
              onSelectColumn={(key, column) =>
                setSelectedColumn((current) =>
                  current?.key === key && current.column === column
                    ? null
                    : { key, column },
                )
              }
            />
          </div>
          {selected && selectedEdges.length > 0 && (
            <EdgeDetails
              ws={ws}
              edges={selectedEdges}
              selected={selected}
              nodesByKey={nodesByKey}
              showColumns={expanded.size > 0}
            />
          )}
        </>
      ) : graph.hidden ? (
        // The case this whole signal exists for. Telling someone "nothing
        // depends on this" when something does — and they simply cannot see it —
        // is the one wrong answer lineage must never give, because it is the
        // answer people act on.
        <EmptyState
          icon={EyeOff}
          title="This table's lineage is outside this workspace"
          description="There are relationships here, but every one of them reaches a catalog this workspace does not attach. Nothing about them can be shown."
        />
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
