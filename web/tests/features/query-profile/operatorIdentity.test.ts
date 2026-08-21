import { describe, it, expect } from "vitest";
import {
  operatorClass,
  operatorClassBreakdown,
  operatorIdentity,
  scanEffectiveness,
} from "@/features/query-profile/operatorIdentity";
import type { QueryProfileNode } from "@/types/query";

/** A profile node with the fields the normalizer always sets. */
function node(over: Partial<QueryProfileNode> = {}): QueryProfileNode {
  return {
    type: "PROJECTION",
    name: "PROJECTION",
    estimated_cardinality: null,
    rows_scanned: null,
    rows_produced: null,
    time_ms: null,
    result_bytes: null,
    extra_info: {},
    children: [],
    ...over,
  };
}

describe("operatorIdentity", () => {
  it("names the relation a catalog scan read", () => {
    // Verified against DuckDB 1.5.5: a native scan reports the fully qualified
    // table in extra_info.Table, so no agent-side enrichment is needed.
    const n = node({
      type: "TABLE_SCAN",
      name: "SEQ_SCAN",
      extra_info: { Table: "memory.main.customer", Type: "Sequential Scan" },
    });
    expect(operatorIdentity(n)).toBe("Scan memory.main.customer");
  });

  it("recovers the relation from an Iceberg data path", () => {
    // An ICEBERG_SCAN carries no Table key — verified across 16k real scans —
    // and its file name is a UUID, so the useful name is in the path.
    const n = node({
      type: "TABLE_SCAN",
      name: "ICEBERG_SCAN",
      extra_info: {
        Function: "ICEBERG_SCAN",
        "Total Files Read": "1",
        "Filename(s)":
          "s3://warehouse/tpch/sf10/nation/data/019fdcf1-8747-74ba-a656-8f87c04a1580.parquet",
      },
    });
    expect(operatorIdentity(n)).toBe("Scan sf10.nation");
  });

  it("falls back to the file name for a path that is not Iceberg-shaped", () => {
    const n = node({
      type: "TABLE_SCAN",
      name: "PARQUET_SCAN",
      extra_info: {
        Function: "PARQUET_SCAN",
        "Total Files Read": "1",
        "Filename(s)": "/tmp/exports/orders.parquet",
      },
    });
    expect(operatorIdentity(n)).toBe("Scan orders.parquet");
  });

  it("says how many files a multi-file scan read", () => {
    // Filename(s) is a comma-separated list when several files were read.
    const n = node({
      type: "TABLE_SCAN",
      name: "ICEBERG_SCAN",
      extra_info: {
        Function: "ICEBERG_SCAN",
        "Total Files Read": "5",
        "Filename(s)":
          "s3://warehouse/tpch/sf100/supplier/data/a.parquet, s3://warehouse/tpch/sf100/supplier/data/b.parquet",
      },
    });
    expect(operatorIdentity(n)).toBe("Scan sf100.supplier (5 files)");
  });

  it("reports only a file count for Iceberg, which has no pruning ratio", () => {
    const n = node({
      type: "TABLE_SCAN",
      name: "ICEBERG_SCAN",
      rows_produced: 25,
      extra_info: { Function: "ICEBERG_SCAN", "Total Files Read": "1" },
    });
    const eff = scanEffectiveness(n)!;
    expect(eff.filesRead).toBe(1);
    // Nothing is invented: DuckHaven's own storage reports no files-considered.
    expect(eff.filesConsidered).toBeUndefined();
  });

  it("identifies a join by its type and condition", () => {
    const n = node({
      type: "HASH_JOIN",
      name: "HASH_JOIN",
      extra_info: { "Join Type": "INNER", Conditions: "o_custkey = c_custkey" },
    });
    expect(operatorIdentity(n)).toBe("Inner join on o_custkey = c_custkey");
  });

  it("identifies aggregates by their grouping keys", () => {
    expect(
      operatorIdentity(
        node({
          type: "HASH_GROUP_BY",
          name: "HASH_GROUP_BY",
          extra_info: { Groups: ["c_name", "o_status"] },
        }),
      ),
    ).toBe("Group by c_name, o_status");

    expect(
      operatorIdentity(
        node({
          type: "UNGROUPED_AGGREGATE",
          name: "UNGROUPED_AGGREGATE",
          extra_info: { Aggregates: "sum(#0)" },
        }),
      ),
    ).toBe("Aggregate sum(#0)");
  });

  it("identifies sorts and top-n", () => {
    expect(
      operatorIdentity(
        node({
          type: "ORDER_BY",
          name: "ORDER_BY",
          extra_info: { "Order By": "count_star() DESC" },
        }),
      ),
    ).toBe("Sort by count_star() DESC");

    expect(
      operatorIdentity(
        node({
          type: "TOP_N",
          name: "TOP_N",
          extra_info: { Top: "10", "Order By": "count_star() DESC" },
        }),
      ),
    ).toBe("Top 10 by count_star() DESC");
  });

  it("reads an extra_info value whether it is a string or a list", () => {
    // DuckDB spells the same key both ways for the same operator depending on
    // how many values there are, and the repo's own two fixtures disagree
    // about it. Both have to work.
    const asList = node({
      type: "HASH_GROUP_BY",
      extra_info: { Groups: ["a", "b"] },
    });
    const asString = node({
      type: "HASH_GROUP_BY",
      extra_info: { Groups: "a, b" },
    });
    expect(operatorIdentity(asList)).toBe(operatorIdentity(asString));
  });

  it("degrades to the operator name rather than throwing", () => {
    // BATCH_CREATE_TABLE_AS really does emit an empty extra_info.
    expect(
      operatorIdentity(
        node({ type: "BATCH_CREATE_TABLE_AS", name: "BATCH_CREATE_TABLE_AS" }),
      ),
    ).toBe("BATCH_CREATE_TABLE_AS");

    // A scan with nothing identifying in it at all.
    expect(
      operatorIdentity(node({ type: "TABLE_SCAN", name: "SEQ_SCAN" })),
    ).toBe("Scan SEQ_SCAN");

    // An unrecognized operator keeps the old behaviour.
    expect(
      operatorIdentity(node({ type: "FUTURE_OP", name: "FUTURE_OP" })),
    ).toBe("FUTURE_OP");
  });

  it("survives a malformed node", () => {
    const broken = {
      type: "TABLE_SCAN",
      name: "",
      extra_info: null,
      children: null,
    } as unknown as QueryProfileNode;
    expect(() => operatorIdentity(broken)).not.toThrow();
  });

  it("truncates a long condition instead of overflowing the row", () => {
    const n = node({
      type: "HASH_JOIN",
      extra_info: {
        "Join Type": "INNER",
        Conditions: "a".repeat(200),
      },
    });
    expect(operatorIdentity(n).length).toBeLessThan(90);
    expect(operatorIdentity(n).endsWith("…")).toBe(true);
  });
});

describe("operatorClass", () => {
  it.each([
    ["TABLE_SCAN", "SEQ_SCAN", "scan"],
    ["TABLE_SCAN", "PARQUET_SCAN", "scan"],
    ["TABLE_SCAN", "ICEBERG_SCAN", "scan"],
    ["HASH_JOIN", "HASH_JOIN", "join"],
    ["NESTED_LOOP_JOIN", "NESTED_LOOP_JOIN", "join"],
    ["HASH_GROUP_BY", "HASH_GROUP_BY", "aggregate"],
    ["PERFECT_HASH_GROUP_BY", "PERFECT_HASH_GROUP_BY", "aggregate"],
    ["UNGROUPED_AGGREGATE", "UNGROUPED_AGGREGATE", "aggregate"],
    ["ORDER_BY", "ORDER_BY", "sort"],
    ["TOP_N", "TOP_N", "sort"],
    ["WINDOW", "WINDOW", "window"],
    ["PROJECTION", "PROJECTION", "other"],
  ])("classes %s/%s as %s", (type, name, expected) => {
    expect(operatorClass(node({ type, name }))).toBe(expected);
  });
});

describe("operatorClassBreakdown", () => {
  it("sums self time by class, largest first", () => {
    const tree = node({
      type: "ORDER_BY",
      time_ms: 20,
      children: [
        node({
          type: "HASH_JOIN",
          time_ms: 10,
          children: [
            node({ type: "TABLE_SCAN", name: "SEQ_SCAN", time_ms: 60 }),
            node({ type: "TABLE_SCAN", name: "PARQUET_SCAN", time_ms: 10 }),
          ],
        }),
      ],
    });

    const shares = operatorClassBreakdown(tree);
    expect(shares.map((s) => s.cls)).toEqual(["scan", "sort", "join"]);
    expect(shares[0].timeMs).toBe(70);
    expect(shares[0].pct).toBeCloseTo(70);
    // Shares of summed operator time, so they total 100 — deliberately not a
    // share of latency, which parallel operators would overshoot.
    expect(shares.reduce((a, s) => a + s.pct, 0)).toBeCloseTo(100);
  });

  it("returns nothing when no operator reported a time", () => {
    expect(operatorClassBreakdown(node({ time_ms: null }))).toEqual([]);
    expect(operatorClassBreakdown(null)).toEqual([]);
  });
});

describe("scanEffectiveness", () => {
  it("reports the files-read ratio DuckDB actually provides", () => {
    // Verified on 1.5.5: a multi-file read reports "Scanning Files": "1/10".
    const n = node({
      type: "TABLE_SCAN",
      name: "READ_PARQUET",
      rows_produced: 100,
      extra_info: {
        "Scanning Files": "1/10",
        "File Filters": "(p = 3)",
        "Total Files Read": "1",
      },
    });
    expect(scanEffectiveness(n)).toEqual({
      filesRead: 1,
      filesConsidered: 10,
      rowsProduced: 100,
      pushedFilters: "(p = 3)",
    });
  });

  it("falls back to files read when no pruning ratio is reported", () => {
    const n = node({
      type: "TABLE_SCAN",
      name: "PARQUET_SCAN",
      rows_produced: 5,
      extra_info: { "Total Files Read": "1" },
    });
    expect(scanEffectiveness(n)).toMatchObject({
      filesRead: 1,
      rowsProduced: 5,
    });
  });

  it("never reports rows scanned, which is unreliable for file scans", () => {
    // A 200k-row Parquet file reports 1,600,000 rows scanned regardless of
    // projection or filter selectivity, so a rows-read-versus-returned ratio
    // built on it would be fiction for exactly the storage DuckHaven reads.
    const n = node({
      type: "TABLE_SCAN",
      name: "PARQUET_SCAN",
      rows_scanned: 1_600_000,
      rows_produced: 1,
      extra_info: { "Total Files Read": "1" },
    });
    expect(JSON.stringify(scanEffectiveness(n))).not.toContain("1600000");
  });

  it("is null for anything that is not a scan", () => {
    expect(scanEffectiveness(node({ type: "HASH_JOIN" }))).toBeNull();
  });
});
