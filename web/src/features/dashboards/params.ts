import type { SkipReason } from "@/types/dashboard";

/**
 * A dashboard's parameters as the URL holds them, `?p.period=last_7_days`, so
 * a filtered view can be bookmarked and shared. A value of null asks for no
 * filter where the file sets a default.
 */
export type DashboardSearch = Record<string, string | number | boolean | null>;

const PREFIX = "p.";

/** The `p.*` entries of a URL's search; anything else is dropped. */
export function parseDashboardSearch(
  search: Record<string, unknown>,
): DashboardSearch {
  const out: DashboardSearch = {};
  for (const [key, value] of Object.entries(search)) {
    if (!key.startsWith(PREFIX) || key.length === PREFIX.length) continue;
    if (
      value === null ||
      typeof value === "string" ||
      typeof value === "number" ||
      typeof value === "boolean"
    ) {
      out[key] = value;
    }
  }
  return out;
}

/** The URL's parameters as a run request's `params`. */
export function runParams(search: DashboardSearch): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(search).map(([key, value]) => [
      key.slice(PREFIX.length),
      value,
    ]),
  );
}

export function searchKey(name: string): string {
  return `${PREFIX}${name}`;
}

/** The time windows a time_range parameter offers, in the order they are listed. */
export const TIME_PRESETS: [string, string][] = [
  ["today", "Today"],
  ["last_7_days", "Last 7 days"],
  ["last_30_days", "Last 30 days"],
  ["last_90_days", "Last 90 days"],
  ["last_12_months", "Last 12 months"],
  ["week_to_date", "Week to date"],
  ["month_to_date", "Month to date"],
  ["quarter_to_date", "Quarter to date"],
  ["year_to_date", "Year to date"],
  ["last_week", "Last week"],
  ["last_month", "Last month"],
  ["last_quarter", "Last quarter"],
  ["last_year", "Last year"],
];

/** Why a widget was not filtered by a parameter, completing "Not filtered by X: …". */
export const SKIP_REASONS: Record<SkipReason, string> = {
  ignored: "this widget is set to ignore it",
  own_dimension: "it is what this widget breaks down by",
  other_model: "this widget reads another model",
  unreachable: "this widget's metrics are not linked to it",
  not_bound: "this widget's SQL does not use it",
};
