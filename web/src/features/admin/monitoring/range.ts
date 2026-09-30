import type { MonitoringRange, MonitoringWindow } from "@/types/agent";
import { MONITORING_WINDOWS } from "@/types/agent";

/**
 * The Monitoring tab's range, kept in the URL so a zoomed-in incident can be
 * bookmarked and shared: `?range=24h`, or `?from=…&to=…` after a drag-to-zoom.
 */
export interface MonitoringSearch {
  range?: MonitoringWindow;
  from?: string;
  to?: string;
}

export const DEFAULT_WINDOW: MonitoringWindow = "8h";
/** Narrowest range a drag can zoom to; the API refuses anything under 5 minutes. */
export const MIN_ZOOM_MS = 10 * 60_000;

function isIso(value: unknown): value is string {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

/** Unrecognised values fall back to the default: a stale link still renders. */
export function parseMonitoringSearch(
  search: Record<string, unknown>,
): MonitoringSearch {
  const out: MonitoringSearch = {};
  if (MONITORING_WINDOWS.includes(search.range as MonitoringWindow)) {
    out.range = search.range as MonitoringWindow;
  }
  if (
    isIso(search.from) &&
    isIso(search.to) &&
    Date.parse(search.to) - Date.parse(search.from) >= MIN_ZOOM_MS
  ) {
    out.from = search.from;
    out.to = search.to;
  }
  return out;
}

export function rangeFromSearch(search: MonitoringSearch): MonitoringRange {
  if (search.from && search.to) return { start: search.from, end: search.to };
  return { window: search.range ?? DEFAULT_WINDOW };
}
