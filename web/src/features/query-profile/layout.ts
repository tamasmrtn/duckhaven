// Tidy layered layout for a DuckDB plan tree: the result/root operator sits at
// the top and children fan out below (data flows upward, scans at the bottom —
// the conventional direction for a query plan). Leaves take sequential
// horizontal slots and each parent is centered over its children, so subtrees
// never overlap.
//
// DuckDB plans are small trees (single root, fan-in only at joins), so this
// linear post-order pass is plenty — no external graph/layout dependency.

import type { QueryProfileNode } from "@/types/query";

export const NODE_WIDTH = 200;
export const NODE_HEIGHT = 64;
const H_GAP = 36;
const V_GAP = 56;

export interface GraphNode {
  id: string; // stable path id, e.g. "0", "0.1"
  node: QueryProfileNode;
  x: number; // center x
  y: number; // top y
  depth: number;
}

export interface GraphEdge {
  from: string; // parent id
  to: string; // child id
}

export interface GraphLayout {
  nodes: GraphNode[];
  edges: GraphEdge[];
  width: number;
  height: number;
}

export function layoutTree(root: QueryProfileNode): GraphLayout {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  let nextLeaf = 0;

  function place(node: QueryProfileNode, depth: number, id: string): GraphNode {
    const y = depth * (NODE_HEIGHT + V_GAP);
    let x: number;
    if (node.children.length === 0) {
      x = nextLeaf * (NODE_WIDTH + H_GAP) + NODE_WIDTH / 2;
      nextLeaf += 1;
    } else {
      const children = node.children.map((child, i) =>
        place(child, depth + 1, `${id}.${i}`),
      );
      x = (children[0].x + children[children.length - 1].x) / 2;
      for (const child of children) edges.push({ from: id, to: child.id });
    }
    const gn: GraphNode = { id, node, x, y, depth };
    nodes.push(gn);
    return gn;
  }

  place(root, 0, "0");

  const maxX = Math.max(NODE_WIDTH / 2, ...nodes.map((n) => n.x));
  const maxY = Math.max(0, ...nodes.map((n) => n.y));
  return {
    nodes,
    edges,
    width: maxX + NODE_WIDTH / 2,
    height: maxY + NODE_HEIGHT,
  };
}
