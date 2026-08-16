import { describe, expect, it } from "vitest";
import {
  layoutLineage,
  NODE_HEIGHT,
  NODE_WIDTH,
  ROW_HEIGHT,
} from "@/features/lineage/layout";
import type { LineageColumn, LineageEdge, LineageNode } from "@/types/lineage";

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

function column(source: string, target: string): LineageColumn {
  return {
    source_column: source,
    target_column: target,
    providers: ["execution"],
    stale: false,
  };
}

function edge(
  source: string,
  target: string,
  columns: LineageColumn[] = [],
): LineageEdge {
  return {
    source_key: source,
    target_key: target,
    operation: "create_table_as",
    providers: [
      {
        name: "execution",
        first_seen_at: "2026-01-01T00:00:00Z",
        last_seen_at: "2026-01-02T00:00:00Z",
        observation_count: 1,
        stale: false,
        column_lineage: columns.length > 0 ? "derived" : "unknown",
      },
    ],
    confidence: "exact",
    first_seen_at: "2026-01-01T00:00:00Z",
    last_seen_at: "2026-01-02T00:00:00Z",
    observation_count: 1,
    stale: false,
    last_query_id: null,
    columns,
    column_lineage: columns.length > 0 ? "derived" : "unknown",
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

describe("layoutLineage with expanded nodes", () => {
  const columns = [column("a", "x"), column("b", "y")];

  it("keeps nodes at the collapsed height when nothing is expanded", () => {
    const layout = layoutLineage(
      [node("src", -1), node("dst", 0)],
      [edge("src", "dst", columns)],
    );

    expect(layout.nodes.every((n) => n.height === NODE_HEIGHT)).toBe(true);
    expect(layout.nodes.every((n) => n.rows.length === 0)).toBe(true);
    expect(layout.edges[0].columnLinks).toEqual([]);
  });

  it("gives an expanded node a row per participating column", () => {
    const layout = layoutLineage(
      [node("src", -1), node("dst", 0)],
      [edge("src", "dst", columns)],
      new Set(["src"]),
    );

    const src = layout.nodes.find((n) => n.id === "src")!;
    expect(src.rows.map((r) => r.column)).toEqual(["a", "b"]);
    expect(src.height).toBeGreaterThan(NODE_HEIGHT);
    // Its rows sit below the header, in order.
    expect(src.rows[0].y).toBeGreaterThan(NODE_HEIGHT);
    expect(src.rows[1].y - src.rows[0].y).toBe(ROW_HEIGHT);
  });

  it("lists only the columns that take part in this graph", () => {
    const layout = layoutLineage(
      [node("src", -1), node("dst", 0)],
      [edge("src", "dst", [column("a", "x")])],
      new Set(["src", "dst"]),
    );

    expect(
      layout.nodes.find((n) => n.id === "src")!.rows.map((r) => r.column),
    ).toEqual(["a"]);
    expect(
      layout.nodes.find((n) => n.id === "dst")!.rows.map((r) => r.column),
    ).toEqual(["x"]);
  });

  it("draws column links only once both endpoints are open", () => {
    const nodes = [node("src", -1), node("dst", 0)];
    const edges = [edge("src", "dst", columns)];

    const halfOpen = layoutLineage(nodes, edges, new Set(["src"]));
    expect(halfOpen.edges[0].columnLinks).toEqual([]);

    const bothOpen = layoutLineage(nodes, edges, new Set(["src", "dst"]));
    expect(bothOpen.edges[0].columnLinks).toHaveLength(2);
  });

  it("anchors each column link on its own row", () => {
    const layout = layoutLineage(
      [node("src", -1), node("dst", 0)],
      [edge("src", "dst", columns)],
      new Set(["src", "dst"]),
    );

    const src = layout.nodes.find((n) => n.id === "src")!;
    const dst = layout.nodes.find((n) => n.id === "dst")!;
    const [first, second] = layout.edges[0].columnLinks;

    expect(first.fromY).toBe(src.y + src.rows[0].y);
    expect(first.toY).toBe(dst.y + dst.rows[0].y);
    expect(second.fromY).not.toBe(first.fromY);
  });

  it("stacks a column around a tall expanded node without overlapping", () => {
    const layout = layoutLineage(
      [node("a", -1), node("b", -1), node("dst", 0)],
      [edge("a", "dst", columns), edge("b", "dst", [column("c", "z")])],
      new Set(["a"]),
    );

    const a = layout.nodes.find((n) => n.id === "a")!;
    const b = layout.nodes.find((n) => n.id === "b")!;
    expect(a.height).toBeGreaterThan(b.height);
    expect(b.y).toBeGreaterThanOrEqual(a.y + a.height);
  });

  it("grows the canvas to fit the tallest expanded column", () => {
    const nodes = [node("src", -1), node("dst", 0)];
    const edges = [edge("src", "dst", columns)];

    const collapsed = layoutLineage(nodes, edges);
    const opened = layoutLineage(nodes, edges, new Set(["src"]));

    expect(opened.height).toBeGreaterThan(collapsed.height);
    expect(opened.width).toBe(collapsed.width);
  });

  it("ignores an expanded node that has no column detail", () => {
    const layout = layoutLineage(
      [node("src", -1), node("dst", 0)],
      [edge("src", "dst")],
      new Set(["src"]),
    );

    const src = layout.nodes.find((n) => n.id === "src")!;
    expect(src.rows).toEqual([]);
    expect(src.height).toBe(NODE_HEIGHT);
    expect(src.expanded).toBe(false);
  });
});
