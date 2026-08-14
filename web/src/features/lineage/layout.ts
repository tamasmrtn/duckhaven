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

import type { LineageEdge, LineageNode } from "@/types/lineage";

export const NODE_WIDTH = 190;
export const NODE_HEIGHT = 58;
const H_GAP = 72;
const V_GAP = 22;

export interface LineageGraphNode {
  id: string; // the asset key
  node: LineageNode;
  x: number; // center x
  y: number; // top y
  column: number; // signed distance
}

export interface LineageGraphEdge {
  from: string;
  to: string;
  edge: LineageEdge;
}

export interface LineageGraphLayout {
  nodes: LineageGraphNode[];
  edges: LineageGraphEdge[];
  width: number;
  height: number;
}

export function layoutLineage(
  nodes: LineageNode[],
  edges: LineageEdge[],
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

  const sortedColumns = [...columns.keys()].sort((a, b) => a - b);
  const tallest = Math.max(...[...columns.values()].map((c) => c.length));
  const height = tallest * NODE_HEIGHT + (tallest - 1) * V_GAP;

  const placed: LineageGraphNode[] = [];
  sortedColumns.forEach((column, columnIndex) => {
    const bucket = columns.get(column) ?? [];
    const columnHeight =
      bucket.length * NODE_HEIGHT + (bucket.length - 1) * V_GAP;
    // Centre each column vertically so a short column sits opposite the middle
    // of a tall one rather than hugging the top.
    const top = (height - columnHeight) / 2;
    bucket.forEach((node, rowIndex) => {
      placed.push({
        id: node.key,
        node,
        x: columnIndex * (NODE_WIDTH + H_GAP) + NODE_WIDTH / 2,
        y: top + rowIndex * (NODE_HEIGHT + V_GAP),
        column,
      });
    });
  });

  // Drop edges whose endpoints were not returned — a truncated walk can name an
  // endpoint it never expanded, and a dangling edge would render as a line into
  // nowhere.
  const known = new Set(placed.map((n) => n.id));
  const laidOut = edges
    .filter((e) => known.has(e.source_key) && known.has(e.target_key))
    .map((e) => ({ from: e.source_key, to: e.target_key, edge: e }));

  return {
    nodes: placed,
    edges: laidOut,
    width: sortedColumns.length * (NODE_WIDTH + H_GAP) - H_GAP,
    height,
  };
}
