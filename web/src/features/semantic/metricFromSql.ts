import type { Aggregation } from "@/types/semantic";

export type SeededMetric = {
  agg: Aggregation;
  /** The expression to aggregate — the inner argument, not the whole call. */
  expr: string;
  /** A name taken from a trailing alias, when the selection carried one. */
  name: string;
};

const AGGS: Record<string, Aggregation> = {
  sum: "sum",
  count: "count",
  avg: "avg",
  average: "avg",
  min: "min",
  max: "max",
};

/** Whether `(` at index 0 closes at the final character, rather than earlier. */
function wrapsWhole(text: string): boolean {
  let depth = 0;
  for (let i = 0; i < text.length; i++) {
    if (text[i] === "(") depth++;
    else if (text[i] === ")") {
      depth--;
      if (depth === 0) return i === text.length - 1;
    }
  }
  return false;
}

/**
 * Seed a metric from a highlighted SQL expression.
 *
 * A metric stores its aggregation and the expression separately — `agg: "sum"`
 * over `expr: "total_amount"`, never the string `SUM(total_amount)` — so the
 * compiler owns the arithmetic. Someone selecting `SUM(total_amount)` in a
 * worksheet means exactly that pair, and making them retype it split in two is
 * the kind of friction that stops definitions getting written down at all.
 *
 * This only splits what it can prove: an aggregate call whose parenthesis wraps
 * the entire selection. Anything else — an arithmetic expression, two aggregates
 * added together, a bare column — comes back as the whole text in `expr` with
 * `sum` as the starting point, which is a prefilled form to correct rather than
 * a guess presented as fact. The dialog shows both fields, so a wrong seed is
 * visible before it is saved.
 */
export function metricFromSql(sql: string): SeededMetric {
  let text = sql
    .trim()
    .replace(/;+\s*$/, "")
    .trim();

  // `SUM(total_amount) AS revenue` — the alias is the name the author already
  // chose, so it should not have to be typed again.
  let name = "";
  const aliased = /\s+as\s+("?)([a-z_][a-z0-9_]*)\1$/i.exec(text);
  if (aliased) {
    name = aliased[2];
    text = text.slice(0, aliased.index).trim();
  }

  const call = /^([a-z_]+)\s*\(([\s\S]*)\)$/i.exec(text);
  if (call && wrapsWhole(text.slice(call[1].length).trim())) {
    const agg = AGGS[call[1].toLowerCase()];
    let inner = call[2].trim();
    if (agg === "count") {
      const distinct = /^distinct\s+([\s\S]+)$/i.exec(inner);
      if (distinct)
        return { agg: "count_distinct", expr: distinct[1].trim(), name };
      // `COUNT(*)` has no expression; the API treats `count` as the one
      // aggregation that does not need one.
      if (inner === "*") inner = "";
    }
    if (agg) return { agg, expr: inner, name };
  }

  return { agg: "sum", expr: text, name };
}
