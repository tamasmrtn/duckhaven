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

/** Far more rows read than the query ultimately returned (missing pushdown). */
export function isScanBlowUp(
  node: QueryProfileNode,
  summary: QueryProfileSummary,
): boolean {
  const scanned = node.rows_scanned ?? 0;
  return (
    scanned > SCAN_MIN_ROWS &&
    summary.rows_returned > 0 &&
    scanned > SCAN_BLOWUP_RATIO * summary.rows_returned
  );
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
