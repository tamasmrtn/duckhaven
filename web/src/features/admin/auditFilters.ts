/**
 * The Audit page's filter state, which lives in the URL.
 *
 * The same rules as History's (see features/history/filters): defaults are left
 * out of the URL, and an unrecognized value falls back to the default instead of
 * erroring, so a stale or hand-edited link still renders. The cursor is not in
 * the URL either: a shared link opens the filtered view at its first page.
 */
import {
  AUDIT_CATEGORIES,
  AUDIT_OUTCOMES,
  type AuditCategory,
  type AuditOutcome,
} from "@/types/audit";
import {
  RANGE_LABELS,
  TIME_RANGES,
  type TimeRange,
  rangeBoundary,
} from "@/features/history/filters";

export const CATEGORY_LABELS: Record<AuditCategory, string> = {
  identity: "Accounts",
  access: "Access",
  credential: "Tokens",
  auth: "Sign-ins",
  authz: "Refusals",
};

export const OUTCOME_LABELS: Record<AuditOutcome, string> = {
  success: "Succeeded",
  failure: "Failed",
  denied: "Denied",
};

/**
 * Thirty days, not History's seven: access changes are rare next to queries, so
 * a week is often empty, and someone opening the audit log is usually looking
 * for one change they know happened.
 */
export const DEFAULT_RANGE: TimeRange = "30d";

export interface AuditSearch {
  range?: TimeRange;
  since?: string;
  until?: string;
  category?: AuditCategory;
  outcome?: AuditOutcome;
  /** A user id: events they made, or that were made on their behalf. */
  actor?: string;
  /** A user id: events that changed their access. */
  subject?: string;
  /** A workspace slug. */
  workspace?: string;
  /** Only changes people made to their own access. Absent rather than false. */
  own?: true;
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
export function parseAuditSearch(search: Record<string, unknown>): AuditSearch {
  // The router parses `?own=true` as a boolean, a hand-typed link as a string.
  const own = search.own === true || search.own === "true";
  return {
    range: oneOf(search.range, TIME_RANGES),
    since: str(search.since),
    until: str(search.until),
    category: oneOf(search.category, AUDIT_CATEGORIES),
    outcome: oneOf(search.outcome, AUDIT_OUTCOMES),
    actor: str(search.actor),
    subject: str(search.subject),
    workspace: str(search.workspace),
    own: own || undefined,
  };
}

export interface ResolvedAuditFilters {
  range: TimeRange;
  since?: string;
  until?: string;
  /** What the list covers, e.g. "Access · Last 30 days". */
  scopeLabel: string;
  hasFilters: boolean;
}

export function resolveAuditFilters(
  search: AuditSearch,
  now: number = Date.now(),
): ResolvedAuditFilters {
  const range = search.range ?? DEFAULT_RANGE;
  const custom = range === "custom";
  const what = search.category
    ? CATEGORY_LABELS[search.category]
    : "All events";
  return {
    range,
    since: custom ? search.since : rangeBoundary(range, now),
    until: custom ? search.until : undefined,
    scopeLabel: `${what} · ${RANGE_LABELS[range]}`,
    hasFilters: Object.values(search).some((v) => v != null),
  };
}
