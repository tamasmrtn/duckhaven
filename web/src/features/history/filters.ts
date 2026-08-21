/**
 * The History page's filter state, which lives in the URL.
 *
 * Putting it there rather than in component state is what makes a filtered view
 * something you can bookmark, reload, or paste to a colleague. Two rules keep
 * that URL readable:
 *
 * - **Defaults are omitted.** A bare `/$ws/history` is the default view, not a
 *   view with fifteen parameters spelled out.
 * - **Invalid values fall back rather than erroring.** A stale or hand-edited
 *   link should render the default view, not an error page. The *server* still
 *   rejects a bad value with a 422, which is what protects API callers; the URL
 *   is a UI surface and forgives.
 *
 * The cursor is deliberately absent: a shared link reproduces the filtered view
 * from its first page, not someone else's scroll position.
 */

export const TIME_RANGES = ["1h", "24h", "7d", "30d", "all", "custom"] as const;
export type TimeRange = (typeof TIME_RANGES)[number];

export const DURATION_UNITS = ["ms", "s", "min"] as const;
export type DurationUnit = (typeof DURATION_UNITS)[number];

export const SORT_KEYS = ["started_at", "duration"] as const;
export type SortKey = (typeof SORT_KEYS)[number];

export const SORT_DIRS = ["asc", "desc"] as const;
export type SortDir = (typeof SORT_DIRS)[number];

export const ORIGINS = ["interactive", "scheduled", "session"] as const;

export const STATUSES = [
  "queued",
  "running",
  "done",
  "failed",
  "cancelled",
] as const;

/** The eleven kinds the API classifies statements into. */
export const STATEMENT_TYPES = [
  "select",
  "insert",
  "update",
  "delete",
  "merge",
  "copy",
  "create",
  "alter",
  "drop",
  "describe",
  "other",
] as const;

export const RANGE_LABELS: Record<TimeRange, string> = {
  "1h": "Last hour",
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  all: "All time",
  custom: "Custom range",
};

const RANGE_MS: Partial<Record<TimeRange, number>> = {
  "1h": 3_600_000,
  "24h": 24 * 3_600_000,
  "7d": 7 * 24 * 3_600_000,
  "30d": 30 * 24 * 3_600_000,
};

const UNIT_MS: Record<DurationUnit, number> = {
  ms: 1,
  s: 1000,
  min: 60_000,
};

export interface HistorySearch {
  q?: string;
  id?: string;
  range?: TimeRange;
  since?: string;
  until?: string;
  /** Comma-joined in the URL; an array everywhere else. */
  status?: string;
  type?: string;
  slower?: number;
  unit?: DurationUnit;
  sort?: SortKey;
  dir?: SortDir;
  /** A user id, or "all". Absent means the caller's own runs. */
  user?: string;
  /** "all" widens past this workspace. Admin-only server-side. */
  scope?: string;
  agent?: string;
  origin?: string;
}

function str(v: unknown): string | undefined {
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

function oneOf<T extends string>(
  v: unknown,
  allowed: readonly T[],
): T | undefined {
  return typeof v === "string" && (allowed as readonly string[]).includes(v)
    ? (v as T)
    : undefined;
}

/** Narrow raw search params into the page's filter state. */
export function parseHistorySearch(
  search: Record<string, unknown>,
): HistorySearch {
  const slower = Number(search.slower);
  return {
    q: str(search.q),
    id: str(search.id),
    range: oneOf(search.range, TIME_RANGES),
    since: str(search.since),
    until: str(search.until),
    status: str(search.status),
    type: str(search.type),
    slower: Number.isFinite(slower) && slower > 0 ? slower : undefined,
    unit: oneOf(search.unit, DURATION_UNITS),
    sort: oneOf(search.sort, SORT_KEYS),
    dir: oneOf(search.dir, SORT_DIRS),
    user: str(search.user),
    scope: oneOf(search.scope, ["ws", "all"] as const),
    agent: str(search.agent),
    origin: oneOf(search.origin, ORIGINS),
  };
}

/** Split a comma-joined multi-value param, dropping anything unrecognized. */
export function splitMulti<T extends string>(
  value: string | undefined,
  allowed: readonly T[],
): T[] {
  if (!value) return [];
  return value
    .split(",")
    .map((v) => v.trim())
    .filter((v): v is T => (allowed as readonly string[]).includes(v));
}

/**
 * Resolve a range to the ISO boundary it means, as of `now`.
 *
 * Rolling from the current instant, not calendar-aligned — which is exactly the
 * ambiguity the preset menu resolves by printing the concrete date beside the
 * label.
 */
export function rangeBoundary(
  range: TimeRange,
  now: number = Date.now(),
): string | undefined {
  const ms = RANGE_MS[range];
  return ms == null ? undefined : new Date(now - ms).toISOString();
}

export function durationToMs(slower: number, unit: DurationUnit): number {
  return Math.round(slower * UNIT_MS[unit]);
}

export interface ResolvedFilters {
  range: TimeRange;
  sort: SortKey;
  dir: SortDir;
  unit: DurationUnit;
  statuses: string[];
  types: string[];
  /** True when scoped to the caller's own runs (the default). */
  isMine: boolean;
  since?: string;
  until?: string;
  /** Description of the active scope, e.g. "My queries · Last 7 days". */
  scopeLabel: string;
}

/**
 * Apply defaults and derive everything the page and the request both need.
 *
 * The default — the caller's own runs from the last 7 days — matches where a
 * user's attention actually is when they open History. It is only a good
 * default because widening it is one visible click away; see the scope line in
 * HistoryPage.
 */
export function resolveFilters(
  search: HistorySearch,
  now: number = Date.now(),
): ResolvedFilters {
  const range = search.range ?? "7d";
  const isMine = search.user == null;
  const custom = range === "custom";
  const since = custom ? search.since : rangeBoundary(range, now);
  const until = custom ? search.until : undefined;

  return {
    range,
    sort: search.sort ?? "started_at",
    dir: search.dir ?? "desc",
    unit: search.unit ?? "s",
    statuses: splitMulti(search.status, STATUSES),
    types: splitMulti(search.type, STATEMENT_TYPES),
    isMine,
    since,
    until,
    scopeLabel: `${isMine ? "My queries" : "All users"} · ${RANGE_LABELS[range]}`,
  };
}
