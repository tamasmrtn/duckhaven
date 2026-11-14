import type {
  Cell,
  ChartMeasure,
  ChartSpec,
  RunColumn,
} from "@/types/dashboard";
import { toNumber } from "./format";

/** One line, or one set of bars, and where its values sit in each datum. */
export interface ChartSeries {
  key: string;
  label: string;
  /** The categorical colour slot, 0 to 7. */
  slot: number;
  measure: ChartMeasure;
}

/** One x value with every series' value there; a series with none is null. */
export type Datum = { x: string } & Record<string, string | number | null>;

export interface ChartData {
  data: Datum[];
  series: ChartSeries[];
}

/**
 * A widget's rows as a chart draws them: one datum per x value, in drawing
 * order, with a value per series.
 *
 * A chart split by a dimension draws one series per key the server chose, in
 * the colour slot it gave that key, so a filter that removes a series never
 * recolours the rest. A chart of several measures draws one series per
 * measure, in slot order. A time axis is drawn oldest first, whatever order
 * the rows came in; categories keep the query's order.
 */
export function chartData(
  chart: ChartSpec,
  columns: RunColumn[],
  rows: Cell[][],
): ChartData {
  const at = (name: string | undefined) =>
    name === undefined ? -1 : columns.findIndex((c) => c.name === name);
  const x = at(chart.x?.field);
  const byX = new Map<string, Datum>();
  const datum = (row: Cell[]) => {
    const key = row[x] === null || row[x] === undefined ? "" : String(row[x]);
    let d = byX.get(key);
    if (!d) {
      d = { x: key } as Datum;
      byX.set(key, d);
    }
    return d;
  };

  let series: ChartSeries[];
  if (chart.series && chart.y.length > 0) {
    const split = at(chart.series.field);
    const y = at(chart.y[0].field);
    series = chart.series.keys.map((key, i) => ({
      key: `s${i}`,
      label: key === "" ? "(none)" : key,
      slot: chart.colors[key] ?? i % 8,
      measure: chart.y[0],
    }));
    const index = new Map(chart.series.keys.map((k, i) => [k, `s${i}`]));
    for (const row of rows) {
      const raw = row[split];
      const key = index.get(
        raw === null || raw === undefined ? "" : String(raw),
      );
      // A series past the eighth is left out; the table view has it.
      if (key === undefined) continue;
      datum(row)[key] = toNumber(row[y] ?? null);
    }
  } else {
    const ys = chart.y.map((m) => at(m.field));
    series = chart.y.map((measure, i) => ({
      key: `s${i}`,
      label: measure.label,
      slot: i % 8,
      measure,
    }));
    for (const row of rows) {
      const d = datum(row);
      ys.forEach((j, i) => {
        d[`s${i}`] = toNumber(row[j] ?? null);
      });
    }
  }

  const data = [...byX.values()];
  for (const d of data) {
    for (const s of series) if (!(s.key in d)) d[s.key] = null;
  }
  if (chart.x?.kind === "time") {
    // ISO dates and timestamps sort as text.
    data.sort((a, b) => (a.x < b.x ? -1 : a.x > b.x ? 1 : 0));
  }
  return { data, series };
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

const STAMP = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/;

/**
 * A point on a time axis named by its period: a month reads "Jun 2026", a
 * week "18 May". `long` is for a tooltip, where there is room to say which
 * year and that a week is meant.
 */
export function formatPeriod(
  value: string,
  grain?: string | null,
  long = false,
): string {
  const m = STAMP.exec(value);
  if (!m) return value;
  const [, year, month, day, hour, minute] = m;
  const mon = MONTHS[Number(month) - 1];
  const date = `${Number(day)} ${mon}`;
  switch (grain) {
    case "year":
      return year;
    case "quarter":
      return `Q${Math.floor((Number(month) - 1) / 3) + 1} ${year}`;
    case "month":
      return `${mon} ${year}`;
    case "week":
      return long ? `Week of ${date} ${year}` : date;
    case "day":
      return long ? `${date} ${year}` : date;
  }
  const time =
    hour && `${hour}:${minute}` !== "00:00" ? ` ${hour}:${minute}` : "";
  return long ? `${date} ${year}${time}` : `${date}${time}`;
}

const FORMULA = /^[=+\-@\t\r]/;

/**
 * Rows as CSV, values as they came rather than as displayed. Text that a
 * spreadsheet would run as a formula is prefixed with an apostrophe.
 */
export function toCsv(columns: RunColumn[], rows: Cell[][]): string {
  const cell = (value: Cell | undefined) => {
    if (value === null || value === undefined) return "";
    if (typeof value !== "string") return String(value);
    const text =
      FORMULA.test(value) && toNumber(value) === null ? `'${value}` : value;
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const lines = [columns.map((c) => cell(c.name)).join(",")];
  for (const row of rows)
    lines.push(columns.map((_, j) => cell(row[j])).join(","));
  return `${lines.join("\r\n")}\r\n`;
}
