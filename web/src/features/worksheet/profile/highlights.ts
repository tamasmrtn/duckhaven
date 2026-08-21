// Inefficiency heuristics computed from a normalized query profile. Pure
// functions so they are trivially unit-testable and reusable by the tree + the
// summary strip. Spill is reported at the query level by DuckDB (per-operator
// temp-dir size is always 0 in 1.5.x), so it is a query-level badge.

import type { QueryProfileNode, QueryProfileSummary } from "@/types/query";

const SCAN_BLOWUP_RATIO = 10;
const SCAN_MIN_ROWS = 10_000;
const EST_RATIO = 10;
const EST_MIN_ROWS = 1_000;
const TIME_HOTSPOT_FRACTION = 0.3;

export type NodeBadge = "scan" | "estimate" | "time";

export const BADGE_LABELS: Record<NodeBadge | "spill", string> = {
  spill: "Spilled to disk",
  scan: "Scan blow-up",
  estimate: "Bad estimate",
  time: "Time hotspot",
};

export function isSpilled(summary: QueryProfileSummary): boolean {
  return summary.spill_bytes > 0;
}

/**
 * Rows DuckDB reported for a scan, corrected for its per-thread double count.
 *
 * `operator_rows_scanned` is not the number of rows read: every thread that
 * takes part in a scan reports the *whole* relation's row count and DuckDB sums
 * them, so the figure is `rows × min(threads, row groups)`. Measured on 1.5.5
 * against one 200,000-row Parquet file, varying only the thread count: 1 thread
 * reported 200,000, two reported 400,000, eight reported 1,600,000.
 *
 * Dividing by the reservation's thread count undoes most of it. It under-
 * corrects when a file has fewer row groups than threads (fewer threads then
 * take part), which is the safe direction: the result is never larger than what
 * was really read. `reserved_threads` is absent on profiles captured before it
 * was recorded, and those are simply left uncorrected.
 */
export function rowsReadByScan(
  node: QueryProfileNode,
  summary: QueryProfileSummary,
): number {
  const reported = node.rows_scanned ?? 0;
  if (!isThreadInflated(node)) return reported;
  const threads = summary.reserved_threads ?? 1;
  return threads > 1 ? Math.round(reported / threads) : reported;
}

/**
 * Whether this node's `rows_scanned` carries the per-thread double count.
 *
 * Only file readers do. Measured on 1.5.5 over the same 200,000-row relation:
 * a `PARQUET_SCAN` reported 200k/400k/800k/1.6M on 1/2/4/8 threads, while a
 * native `SEQ_SCAN` reported 200,000 at every thread count. Dividing a native
 * scan would understate it by up to the thread count — a worse error than the
 * one being corrected, and presented as a corrected fact.
 *
 * A scan of a catalog relation names it in `extra_info.Table`; a file reader
 * has no such key (verified across 16,278 real ICEBERG_SCAN nodes, none of
 * which carried one).
 */
function isThreadInflated(node: QueryProfileNode): boolean {
  const extra = (node?.extra_info ?? {}) as Record<string, unknown>;
  return extra.Table == null;
}

/** True when the displayed figure has been corrected and so needs explaining. */
export function isRowsReadCorrected(
  node: QueryProfileNode,
  summary: QueryProfileSummary,
): boolean {
  return isThreadInflated(node) && (summary.reserved_threads ?? 1) > 1;
}

/** Wording for the tooltip on any corrected rows-read figure. */
export const ROWS_READ_HINT =
  "Rows read by this operator. DuckDB reports this per thread, counting the " +
  "whole relation once per thread that took part, so the raw figure is " +
  "divided by the reservation's thread count.";

/**
 * A scan that read far more rows than *it* emitted — a filter that never made
 * it down into the reader.
 *
 * Compared against the scan's own output, not the query's final row count.
 * Against the latter every aggregate looks pathological: a `GROUP BY` over 600M
 * rows returning 5 is the query working, not a scan misbehaving, and on real
 * data that spelling fired on 84% of queries.
 */
export function isScanBlowUp(
  node: QueryProfileNode,
  summary: QueryProfileSummary,
): boolean {
  const produced = node.rows_produced;
  if (produced == null) return false;
  const read = rowsReadByScan(node, summary);
  // produced === 0 is deliberately not excluded: reading rows to emit none is
  // the strongest form of the thing this looks for.
  return read > SCAN_MIN_ROWS && read > SCAN_BLOWUP_RATIO * produced;
}

/** Actual cardinality diverges sharply from the optimizer's estimate. */
export function isBadEstimate(node: QueryProfileNode): boolean {
  const actual = node.rows_produced;
  const est = node.estimated_cardinality;
  if (actual == null || est == null) return false;
  if (actual < EST_MIN_ROWS && est < EST_MIN_ROWS) return false;
  const ratio = (actual + 1) / (est + 1);
  return ratio > EST_RATIO || ratio < 1 / EST_RATIO;
}

/** Operator that dominates a large fraction of total query time. */
export function isTimeHotspot(
  node: QueryProfileNode,
  summary: QueryProfileSummary,
): boolean {
  if (node.time_ms == null || summary.latency_ms <= 0) return false;
  return node.time_ms / summary.latency_ms > TIME_HOTSPOT_FRACTION;
}

export function nodeBadges(
  node: QueryProfileNode,
  summary: QueryProfileSummary,
): NodeBadge[] {
  const badges: NodeBadge[] = [];
  if (isScanBlowUp(node, summary)) badges.push("scan");
  if (isBadEstimate(node)) badges.push("estimate");
  if (isTimeHotspot(node, summary)) badges.push("time");
  return badges;
}
