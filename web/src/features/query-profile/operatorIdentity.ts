/**
 * One-line identities and coarse classes for profile operators.
 *
 * The ranked "most expensive" list used to render `node.type`, so a three-table
 * join read "TABLE_SCAN, TABLE_SCAN, TABLE_SCAN" and you had to click each one
 * to find out which table was slow.
 *
 * Every key read here was verified against DuckDB 1.5.5 output rather than
 * guessed. Two properties of that output shape this file:
 *
 * - `operator_name` is more specific than `operator_type` for scans
 *   (type `TABLE_SCAN`, name `SEQ_SCAN` / `PARQUET_SCAN` / `READ_PARQUET`), so
 *   the normalizer's `name` is preferred wherever it differs.
 * - `extra_info` values are sometimes a string and sometimes an array of
 *   strings — `"Projections": "k"` versus `["k", "c", "m"]` — for the same key
 *   on the same operator. Everything here goes through `text()`.
 *
 * Nothing throws on a malformed or empty node: `extra_info` is genuinely `{}`
 * for some operators (BATCH_CREATE_TABLE_AS emits nothing), so every path falls
 * back to the operator's own name.
 */

import type { QueryProfileNode } from "@/types/query";

export type OperatorClass =
  "scan" | "join" | "aggregate" | "sort" | "window" | "other";

const MAX_DETAIL = 60;

/** Read an extra_info value that may be a string, an array, or absent. */
function text(extra: Record<string, unknown>, key: string): string | undefined {
  const v = extra?.[key];
  if (v == null) return undefined;
  const s = Array.isArray(v) ? v.filter(Boolean).join(", ") : String(v);
  const trimmed = s.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

function truncate(s: string): string {
  return s.length > MAX_DETAIL ? `${s.slice(0, MAX_DETAIL - 1)}…` : s;
}

/**
 * The relation a scanned data file belongs to, or its file name.
 *
 * An Iceberg data file is written as
 * `<root>/<catalog>/<schema>/<table>/data/<uuid>.parquet`, so the file name
 * itself is a UUID and tells a reader nothing — but the two segments before
 * `/data/` are the schema and table. Verified against 16k real ICEBERG_SCAN
 * nodes, none of which carried a `Table` key to read instead.
 *
 * Falls back to the bare file name when the path is not in that shape, which is
 * what a plain `read_parquet` over an arbitrary path looks like.
 */
function relationFromPath(path: string): string {
  const first = path.split(",")[0].trim();
  const parts = first.split("/").filter(Boolean);
  const dataAt = parts.lastIndexOf("data");
  if (dataAt >= 2 && dataAt === parts.length - 2) {
    return `${parts[dataAt - 2]}.${parts[dataAt - 1]}`;
  }
  return parts[parts.length - 1] || first;
}

const SCAN = /SCAN|SEQ_SCAN|PARQUET|ICEBERG|DELTA|READ_/;
const JOIN = /JOIN/;
const AGGREGATE = /GROUP_BY|AGGREGATE|DISTINCT/;
const SORT = /ORDER_BY|TOP_N|SORT/;
const WINDOW = /WINDOW/;

/** Coarse bucket for the "where did the time go" rollup. */
export function operatorClass(node: QueryProfileNode): OperatorClass {
  const label = `${node.type ?? ""} ${node.name ?? ""}`.toUpperCase();
  // Join before scan: HASH_JOIN never matches SCAN, but a future
  // *_JOIN_SCAN spelling would, and a join misfiled as a scan is worse.
  if (JOIN.test(label)) return "join";
  if (SCAN.test(label)) return "scan";
  if (AGGREGATE.test(label)) return "aggregate";
  if (WINDOW.test(label)) return "window";
  if (SORT.test(label)) return "sort";
  return "other";
}

/**
 * A concise, self-identifying label for one operator.
 *
 * Falls back through `name` to `type` to a placeholder, so an unrecognized
 * operator degrades to what the old code showed rather than to nothing.
 */
export function operatorIdentity(node: QueryProfileNode): string {
  const extra = (node?.extra_info ?? {}) as Record<string, unknown>;
  const name = node?.name || node?.type || "Operator";
  const cls = node ? operatorClass(node) : "other";

  if (cls === "scan") {
    // A catalog scan names its relation outright, fully qualified.
    const table = text(extra, "Table");
    if (table) return `Scan ${truncate(table)}`;

    // A file scan does not, so recover the relation from the path it read.
    const files = text(extra, "Filename(s)");
    if (files) {
      const total = Number(text(extra, "Total Files Read") ?? "1");
      const suffix = total > 1 ? ` (${total} files)` : "";
      return `Scan ${truncate(relationFromPath(files))}${suffix}`;
    }
    const fn = text(extra, "Function");
    return fn ? `Scan ${truncate(fn)}` : `Scan ${name}`;
  }

  if (cls === "join") {
    const kind = text(extra, "Join Type");
    const on = text(extra, "Conditions");
    const label = kind ? `${titleCase(kind)} join` : "Join";
    return on ? `${label} on ${truncate(on)}` : label;
  }

  if (cls === "aggregate") {
    const groups = text(extra, "Groups");
    if (groups) return `Group by ${truncate(groups)}`;
    const aggs = text(extra, "Aggregates");
    if (aggs) return `Aggregate ${truncate(aggs)}`;
    return name;
  }

  if (cls === "sort") {
    const order = text(extra, "Order By");
    const top = text(extra, "Top");
    if (top) return order ? `Top ${top} by ${truncate(order)}` : `Top ${top}`;
    return order ? `Sort by ${truncate(order)}` : name;
  }

  return name;
}

function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

export interface ClassShare {
  cls: OperatorClass;
  timeMs: number;
  pct: number;
}

/**
 * Share of operator time by class, largest first.
 *
 * Deliberately a share of *summed operator self time*, not of latency.
 * `operator_timing` is self time and a parallel plan overlaps its operators, so
 * these do not sum to wall clock and must not be labelled as if they did.
 */
export function operatorClassBreakdown(
  root: QueryProfileNode | null | undefined,
): ClassShare[] {
  const totals = new Map<OperatorClass, number>();
  let total = 0;

  const stack: QueryProfileNode[] = root ? [root] : [];
  while (stack.length > 0) {
    const node = stack.pop()!;
    if (!node) continue;
    const ms = typeof node.time_ms === "number" ? node.time_ms : 0;
    if (ms > 0) {
      const cls = operatorClass(node);
      totals.set(cls, (totals.get(cls) ?? 0) + ms);
      total += ms;
    }
    for (const child of node.children ?? []) stack.push(child);
  }

  if (total <= 0) return [];
  return [...totals.entries()]
    .map(([cls, timeMs]) => ({ cls, timeMs, pct: (timeMs / total) * 100 }))
    .sort((a, b) => b.timeMs - a.timeMs);
}

export interface ScanEffectiveness {
  /** Files actually read, when DuckDB reported a pruning ratio. */
  filesRead?: number;
  filesConsidered?: number;
  rowsProduced?: number;
  /** True when the scan pushed a filter down into the reader. */
  pushedFilters?: string;
}

/**
 * What a scan's own metrics honestly support.
 *
 * DuckDB 1.5.5 emits **no** byte-pruning and no row-group counters, so nothing
 * here is labelled "bytes pruned".
 *
 * A hive-partitioned `read_parquet` reports `Scanning Files: "1/10"`, a real
 * files-read-of-files-considered ratio. An `ICEBERG_SCAN` does not: across 16k
 * real scans it carries only `Total Files Read`. So the ratio is shown when it
 * exists and a plain count when it does not, rather than implying pruning
 * information DuckHaven's own storage never reports.
 *
 * `rows_scanned` is deliberately not used. It is exact for a native `SEQ_SCAN`
 * but not for Parquet: a 200,000-row file reports 1,600,000 rows scanned
 * regardless of how many columns were projected and regardless of filter
 * selectivity, so a "rows read versus rows returned" ratio built on it would be
 * fiction for exactly the storage DuckHaven actually reads.
 */
export function scanEffectiveness(
  node: QueryProfileNode,
): ScanEffectiveness | null {
  if (!node || operatorClass(node) !== "scan") return null;
  const extra = (node.extra_info ?? {}) as Record<string, unknown>;

  const out: ScanEffectiveness = {};
  const scanning = text(extra, "Scanning Files");
  if (scanning && scanning.includes("/")) {
    const [read, considered] = scanning.split("/").map((n) => Number(n.trim()));
    if (Number.isFinite(read)) out.filesRead = read;
    if (Number.isFinite(considered)) out.filesConsidered = considered;
  } else {
    const total = text(extra, "Total Files Read");
    if (total != null && Number.isFinite(Number(total))) {
      out.filesRead = Number(total);
    }
  }

  if (typeof node.rows_produced === "number") {
    out.rowsProduced = node.rows_produced;
  }
  const filters = text(extra, "Filters") ?? text(extra, "File Filters");
  if (filters) out.pushedFilters = truncate(filters);

  return Object.keys(out).length > 0 ? out : null;
}
