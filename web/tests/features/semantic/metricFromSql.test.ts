import { describe, expect, it } from "vitest";
import { metricFromSql } from "@/features/semantic/metricFromSql";

/**
 * The split matters because a metric stores `agg` and `expr` separately — that
 * separation is what stops the aggregation being a string the assistant could
 * rewrite. Seeding it wrong is recoverable (both fields are visible in the
 * dialog), but seeding it wrong *silently* would put the aggregate inside the
 * expression and produce `SUM(SUM(total_amount))` on save.
 */
describe("metricFromSql", () => {
  it("splits an aggregate call into the pair the API stores", () => {
    expect(metricFromSql("SUM(total_amount)")).toEqual({
      agg: "sum",
      expr: "total_amount",
      name: "",
    });
  });

  it("recognises the aggregations regardless of case and spacing", () => {
    expect(metricFromSql("  avg ( price ) ").agg).toBe("avg");
    expect(metricFromSql("MIN(x)").agg).toBe("min");
    expect(metricFromSql("Max(x)").agg).toBe("max");
    expect(metricFromSql("average(x).").agg).toBe("sum"); // not a call at all
  });

  it("maps COUNT(DISTINCT …) to its own aggregation", () => {
    expect(metricFromSql("count(distinct customer_id)")).toEqual({
      agg: "count_distinct",
      expr: "customer_id",
      name: "",
    });
  });

  it("leaves COUNT(*) without an expression", () => {
    // `count` is the one aggregation the API accepts with no expression.
    expect(metricFromSql("COUNT(*)")).toEqual({
      agg: "count",
      expr: "",
      name: "",
    });
  });

  it("takes the name from a trailing alias", () => {
    expect(metricFromSql("SUM(total_amount) AS revenue")).toEqual({
      agg: "sum",
      expr: "total_amount",
      name: "revenue",
    });
    expect(metricFromSql('SUM(x) as "gross_revenue"').name).toBe(
      "gross_revenue",
    );
  });

  it("keeps a compound expression whole instead of mis-splitting it", () => {
    // The regression this guards: a naive `^agg\((.*)\)$` match would report
    // `sum` over `a) + SUM(b`, which saves as nonsense.
    const seeded = metricFromSql("SUM(a) + SUM(b)");

    expect(seeded.expr).toBe("SUM(a) + SUM(b)");
  });

  it("keeps a nested call whole when the parenthesis really does wrap it", () => {
    expect(metricFromSql("SUM(COALESCE(total_amount, 0))")).toEqual({
      agg: "sum",
      expr: "COALESCE(total_amount, 0)",
      name: "",
    });
  });

  it("falls back to the whole selection for anything it cannot prove", () => {
    // A starting point to correct, not a claim about what the user meant.
    expect(metricFromSql("total_amount * quantity")).toEqual({
      agg: "sum",
      expr: "total_amount * quantity",
      name: "",
    });
  });

  it("ignores a trailing semicolon", () => {
    expect(metricFromSql("SUM(total_amount);").expr).toBe("total_amount");
  });

  it("does not treat a non-aggregate function as an aggregation", () => {
    expect(metricFromSql("COALESCE(a, b)")).toEqual({
      agg: "sum",
      expr: "COALESCE(a, b)",
      name: "",
    });
  });
});
