import type { Cell, ParameterSpec, SkipReason } from "@/types/dashboard";

/**
 * A dashboard's view as the URL holds it, so it can be bookmarked and shared:
 * each parameter as `?p.period=last_7_days`, where a value of null asks for no
 * filter where the file sets a default, and present mode as `?present=true`
 * with its refresh interval in minutes as `?refresh=15`.
 */
export type DashboardSearch = Record<string, string | number | boolean | null>;

const PREFIX = "p.";

/** How often present mode can refresh, in minutes. */
export const REFRESH_MINUTES = [5, 15, 30, 60];

/** The view's entries of a URL's search; anything else is dropped. */
export function parseDashboardSearch(
  search: Record<string, unknown>,
): DashboardSearch {
  const out: DashboardSearch = {};
  for (const [key, value] of Object.entries(search)) {
    if (key === "present") {
      if (value === true) out.present = true;
      continue;
    }
    if (key === "refresh") {
      if (typeof value === "number" && REFRESH_MINUTES.includes(value)) {
        out.refresh = value;
      }
      continue;
    }
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
    Object.entries(search)
      .filter(([key]) => key.startsWith(PREFIX))
      .map(([key, value]) => [key.slice(PREFIX.length), value]),
  );
}

export function searchKey(name: string): string {
  return `${PREFIX}${name}`;
}

/** Whether the URL sets any parameter. */
export function hasParameters(search: DashboardSearch): boolean {
  return Object.keys(search).some((key) => key.startsWith(PREFIX));
}

/**
 * The search with one parameter set, or taken out of the URL when `value` is
 * undefined, so the file's default applies.
 */
export function withParameter(
  search: DashboardSearch,
  name: string,
  value: string | number | boolean | null | undefined,
): DashboardSearch {
  const next = { ...search };
  if (value === undefined) delete next[searchKey(name)];
  else next[searchKey(name)] = value;
  return next;
}

/**
 * The search with a dimension parameter set from a click on a bar. Choosing
 * what the file sets leaves the URL, as the filter bar does.
 */
export function withSelection(
  search: DashboardSearch,
  parameters: Record<string, ParameterSpec> | undefined,
  name: string,
  value: Cell,
): DashboardSearch {
  const param = parameters?.[name];
  const fallback = param?.type === "dimension" ? (param.default ?? null) : null;
  return withParameter(search, name, value === fallback ? undefined : value);
}

/** The search without its parameters; present mode and its refresh stay. */
export function withoutParameters(search: DashboardSearch): DashboardSearch {
  return Object.fromEntries(
    Object.entries(search).filter(([key]) => !key.startsWith(PREFIX)),
  );
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
