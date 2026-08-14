import { describe, expect, it } from "vitest";
import { layoutLineage, NODE_WIDTH } from "@/features/lineage/layout";
import type { LineageEdge, LineageNode } from "@/types/lineage";

function node(key: string, distance: number): LineageNode {
  return {
    key,
    kind: "table",
    catalog: "main",
    schema_name: "analytics",
    table: key,
    system: null,
    distance,
  };
}

function edge(source: string, target: string): LineageEdge {
  return {
    source_key: source,
    target_key: target,
    operation: "create_table_as",
    providers: ["execution"],
    confidence: "exact",
    first_seen_at: "2026-01-01T00:00:00Z",
    last_seen_at: "2026-01-02T00:00:00Z",
    observation_count: 1,
    last_query_id: null,
    columns: [],
  };
}

describe("layoutLineage", () => {
  it("returns an empty layout for an empty graph", () => {
    expect(layoutLineage([], [])).toEqual({
      nodes: [],
      edges: [],
      width: 0,
      height: 0,
    });
  });

  it("places nodes in columns by signed distance, upstream on the left", () => {
    const layout = layoutLineage(
      [node("up", -1), node("root", 0), node("down", 1)],
      [edge("up", "root"), edge("root", "down")],
    );

    const x = (id: string) => layout.nodes.find((n) => n.id === id)!.x;
    expect(x("up")).toBeLessThan(x("root"));
    expect(x("root")).toBeLessThan(x("down"));
  });

  it("puts nodes at the same distance in the same column", () => {
    const layout = layoutLineage(
      [node("a", -1), node("b", -1), node("root", 0)],
      [edge("a", "root"), edge("b", "root")],
    );

    const a = layout.nodes.find((n) => n.id === "a")!;
    const b = layout.nodes.find((n) => n.id === "b")!;
    expect(a.x).toBe(b.x);
    expect(a.y).not.toBe(b.y);
  });

  it("places a shared upstream once, not once per path", () => {
    // The case layoutTree cannot express: fan-out then fan-in.
    const layout = layoutLineage(
      [node("src", -2), node("left", -1), node("right", -1), node("sink", 0)],
      [
        edge("src", "left"),
        edge("src", "right"),
        edge("left", "sink"),
        edge("right", "sink"),
      ],
    );

    expect(layout.nodes.filter((n) => n.id === "src")).toHaveLength(1);
    expect(layout.edges).toHaveLength(4);
  });

  it("handles multiple roots in one column", () => {
    const layout = layoutLineage(
      [node("r1", -1), node("r2", -1), node("r3", -1), node("t", 0)],
      [edge("r1", "t"), edge("r2", "t"), edge("r3", "t")],
    );

    const ys = layout.nodes.filter((n) => n.column === -1).map((n) => n.y);
    expect(new Set(ys).size).toBe(3); // no overlaps
  });

  it("centres a short column against a tall one", () => {
    const layout = layoutLineage(
      [node("a", -1), node("b", -1), node("c", -1), node("root", 0)],
      [edge("a", "root"), edge("b", "root"), edge("c", "root")],
    );

    const root = layout.nodes.find((n) => n.id === "root")!;
    const upstream = layout.nodes.filter((n) => n.column === -1);
    const top = Math.min(...upstream.map((n) => n.y));
    const bottom = Math.max(...upstream.map((n) => n.y));
    expect(root.y).toBeGreaterThan(top);
    expect(root.y).toBeLessThan(bottom);
  });

  it("drops edges whose endpoints were not returned", () => {
    // A truncated walk can name an endpoint it never expanded; rendering that
    // would draw a line into nowhere.
    const layout = layoutLineage(
      [node("root", 0)],
      [edge("missing", "root"), edge("root", "also-missing")],
    );

    expect(layout.edges).toEqual([]);
  });

  it("sizes the canvas to the laid-out content", () => {
    const layout = layoutLineage(
      [node("a", -1), node("root", 0)],
      [edge("a", "root")],
    );
    expect(layout.width).toBeGreaterThan(NODE_WIDTH);
    expect(layout.height).toBeGreaterThan(0);
  });

  it("keeps a single node graph renderable", () => {
    const layout = layoutLineage([node("only", 0)], []);
    expect(layout.nodes).toHaveLength(1);
    expect(layout.width).toBe(NODE_WIDTH);
  });
});
