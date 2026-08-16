// Layered layout for a lineage DAG: data flows left to right, so upstream sits
// to the left of the selected table and downstream to its right.
//
// `features/query-profile/layout.ts` cannot be reused here. That one lays out a
// single-root tree where fan-in only ever happens through a node's own children;
// a lineage graph has shared upstreams, multiple roots, and nodes that are
// reachable by more than one path. What it *can* share is the rendering shell,
// which is why the exported shapes below deliberately mirror its GraphNode /
// GraphEdge / GraphLayout.
//
// The column of a node is its signed distance from the root, which the API
// already computed — so this pass only has to order nodes within a column and
// assign pixels. Ordering is by first appearance, which keeps a node near the
// neighbour that introduced it and empirically produces far fewer crossings than
// sorting by name. No external graph/layout dependency.
//
// Nodes are collapsed by default and expand to show their columns, so heights
// vary and stacking has to use each node's own height rather than a constant.

import type { LineageColumn, LineageEdge, LineageNode } from "@/types/lineage";

export const NODE_WIDTH = 190;
export const NODE_HEIGHT = 58;
/** One column row inside an expanded node. */
export const ROW_HEIGHT = 18;
/** Padding above the first column row and below the last. */
export const ROWS_PADDING = 6;
const H_GAP = 72;
const V_GAP = 22;

export interface LineageColumnRow {
  column: string;
  /** Centre of the row, relative to the node's top edge. */
  y: number;
}

export interface LineageGraphNode {
  id: string; // the asset key
  node: LineageNode;
  x: number; // center x
  y: number; // top y
  column: number; // signed distance
  height: number;
  expanded: boolean;
  /** The node's columns that take part in a mapping in this graph. */
  rows: LineageColumnRow[];
}

export interface LineageColumnLink {
  column: LineageColumn;
  /** Absolute y of the source and target rows this link joins. */
  fromY: number;
  toY: number;
}

export interface LineageGraphEdge {
  from: string;
  to: string;
  edge: LineageEdge;
  /**
   * Per-column links, present only when *both* endpoints are expanded.
   *
   * Drawing a column link into a collapsed node would have to land it on the box
   * itself, which reads as "this column feeds the whole table". Falling back to
   * the table-level line until both sides are open keeps a half-expanded graph
   * honest.
   */
  columnLinks: LineageColumnLink[];
}

export interface LineageGraphLayout {
  nodes: LineageGraphNode[];
  edges: LineageGraphEdge[];
  width: number;
  height: number;
}

/**
 * Which of a node's columns appear when it is expanded.
 *
 * Only the ones taking part in a mapping in this graph — not the table's whole
 * schema, which on a wide table would be unreadable and is already listed in the
 * Schema rail beside the tab. A column with no lineage is answered there.
 */
function columnsOf(key: string, edges: LineageEdge[]): string[] {
  const found = new Set<string>();
  for (const edge of edges) {
    if (edge.source_key === key)
      for (const c of edge.columns) found.add(c.source_column);
    if (edge.target_key === key)
      for (const c of edge.columns) found.add(c.target_column);
  }
  return [...found].sort();
}

function nodeHeight(rows: number): number {
  if (rows === 0) return NODE_HEIGHT;
  return NODE_HEIGHT + ROWS_PADDING * 2 + rows * ROW_HEIGHT;
}

export function layoutLineage(
  nodes: LineageNode[],
  edges: LineageEdge[],
  expanded: ReadonlySet<string> = new Set(),
): LineageGraphLayout {
  if (nodes.length === 0) {
    return { nodes: [], edges: [], width: 0, height: 0 };
  }

  // Group by column, preserving the order nodes arrived in.
  const columns = new Map<number, LineageNode[]>();
  for (const node of nodes) {
    const bucket = columns.get(node.distance);
    if (bucket) bucket.push(node);
    else columns.set(node.distance, [node]);
  }

  const rowsByKey = new Map<string, string[]>();
  const heightByKey = new Map<string, number>();
  for (const node of nodes) {
    const isOpen = expanded.has(node.key);
    const cols = isOpen ? columnsOf(node.key, edges) : [];
    rowsByKey.set(node.key, cols);
    heightByKey.set(node.key, nodeHeight(cols.length));
  }

  const columnHeights = new Map<number, number>();
  for (const [column, bucket] of columns) {
    const stacked = bucket.reduce(
      (total, n) => total + (heightByKey.get(n.key) ?? NODE_HEIGHT),
      0,
    );
    columnHeights.set(column, stacked + (bucket.length - 1) * V_GAP);
  }

  const sortedColumns = [...columns.keys()].sort((a, b) => a - b);
  const height = Math.max(...columnHeights.values());

  const placed: LineageGraphNode[] = [];
  sortedColumns.forEach((column, columnIndex) => {
    const bucket = columns.get(column) ?? [];
    // Centre each column vertically so a short column sits opposite the middle
    // of a tall one rather than hugging the top.
    let cursor = (height - (columnHeights.get(column) ?? 0)) / 2;
    for (const node of bucket) {
      const own = heightByKey.get(node.key) ?? NODE_HEIGHT;
      const cols = rowsByKey.get(node.key) ?? [];
      placed.push({
        id: node.key,
        node,
        x: columnIndex * (NODE_WIDTH + H_GAP) + NODE_WIDTH / 2,
        y: cursor,
        column,
        height: own,
        expanded: cols.length > 0,
        rows: cols.map((name, index) => ({
          column: name,
          y: NODE_HEIGHT + ROWS_PADDING + index * ROW_HEIGHT + ROW_HEIGHT / 2,
        })),
      });
      cursor += own + V_GAP;
    }
  });

  // Drop edges whose endpoints were not returned — a truncated walk can name an
  // endpoint it never expanded, and a dangling edge would render as a line into
  // nowhere.
  const byKey = new Map(placed.map((n) => [n.id, n]));
  const laidOut = edges
    .filter((e) => byKey.has(e.source_key) && byKey.has(e.target_key))
    .map((e) => {
      const source = byKey.get(e.source_key)!;
      const target = byKey.get(e.target_key)!;
      const columnLinks: LineageColumnLink[] = [];
      if (source.expanded && target.expanded) {
        for (const column of e.columns) {
          const from = source.rows.find(
            (r) => r.column === column.source_column,
          );
          const to = target.rows.find((r) => r.column === column.target_column);
          if (!from || !to) continue;
          columnLinks.push({
            column,
            fromY: source.y + from.y,
            toY: target.y + to.y,
          });
        }
      }
      return { from: e.source_key, to: e.target_key, edge: e, columnLinks };
    });

  return {
    nodes: placed,
    edges: laidOut,
    width: sortedColumns.length * (NODE_WIDTH + H_GAP) - H_GAP,
    height,
  };
}
