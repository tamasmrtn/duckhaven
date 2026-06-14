import { describe, it, expect } from "vitest";
import { layoutTree, NODE_WIDTH } from "@/features/query-profile/layout";
import type { QueryProfileNode } from "@/types/query";

function node(type: string, children: QueryProfileNode[] = []): QueryProfileNode {
  return {
    type,
    name: type,
    estimated_cardinality: null,
    rows_scanned: null,
    rows_produced: null,
    time_ms: null,
    result_bytes: null,
    extra_info: {},
    children,
  };
}

describe("layoutTree", () => {
  it("stacks a linear chain with stable ids and increasing depth", () => {
    const tree = node("ORDER_BY", [node("HASH_GROUP_BY", [node("SEQ_SCAN")])]);
    const { nodes, edges } = layoutTree(tree);

    expect(nodes.map((n) => n.id).sort()).toEqual(["0", "0.0", "0.0.0"]);
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    expect(byId["0"].depth).toBe(0);
    expect(byId["0.0.0"].depth).toBe(2);
    // A single chain shares one x column; lower depth sits higher (smaller y).
    expect(byId["0"].x).toBe(byId["0.0.0"].x);
    expect(byId["0"].y).toBeLessThan(byId["0.0.0"].y);
    // Edges run parent -> child.
    expect(edges).toContainEqual({ from: "0", to: "0.0" });
    expect(edges).toContainEqual({ from: "0.0", to: "0.0.0" });
  });

  it("centers a parent over two children that do not overlap", () => {
    const tree = node("HASH_JOIN", [node("SEQ_SCAN"), node("SEQ_SCAN")]);
    const { nodes } = layoutTree(tree);
    const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
    const left = byId["0.0"].x;
    const right = byId["0.1"].x;

    expect(right - left).toBeGreaterThanOrEqual(NODE_WIDTH); // no overlap
    expect(byId["0"].x).toBeCloseTo((left + right) / 2); // parent centered
  });
});
