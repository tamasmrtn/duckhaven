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
  /** The categorical colour slot, 0 to 7, or null to draw in the neutral grey. */
  slot: number | null;
  /** The colour the series' rows give, #rrggbb, drawn as given in both themes. */
  color: string | null;
  dash: "solid" | "dotted";
  /** The name drawn at the line's last point, from an `at: last` marker. */
  endLabel: string | null;
  measure: ChartMeasure;
}

/** A point marked by a marker layer, such as a pit stop. */
export interface Mark {
  /** The layer's place among the point layers, which sets how it is drawn. */
  layer: number;
  label: string;
  /** Radius in pixels, 3 to 6, larger for a larger size value. */
  r: number;
  /** The layer's tooltip columns at this point. */
  details: { name: string; value: Cell }[];
}

/**
 * One x value with every series' value there; a series with none is null. On a
 * number axis `xn` holds the x as a number, which is where the axis draws it.
 */
export type Datum = { x: string; xn?: number } & Record<
  string,
  string | number | null
>;

export interface ChartData {
  data: Datum[];
  series: ChartSeries[];
  /** The x values whose period the window holds only in part. */
  partial: Set<string>;
  /** Series key, then x, to the points marked there. */
  marks: Map<string, Map<string, Mark[]>>;
}

/**
 * A widget's rows as a chart draws them: one datum per x value, in drawing
 * order, with a value per series.
 *
 * A chart split by a dimension draws one series per key the server chose, in
 * the colour slot it gave that key, so a filter that removes a series never
 * recolours the rest. A chart of several measures draws one series per
 * measure, in slot order. A time axis is drawn oldest first and a number axis
 * smallest first, whatever order the rows came in; categories keep the query's
 * order.
 *
 * A line over a window that cuts a period, such as the first week of the last
 * 90 days, draws that period dashed: each series' value is also kept as
 * `<key>Whole`, null at a cut period, and `<key>Partial`, set only at a cut
 * period and the points beside it, so the dashed piece joins the solid line.
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
      // A key the server gave no slot, past the eighth, draws in grey.
      slot: chart.colors[key] ?? null,
      color: chart.styles?.[key]?.color ?? null,
      dash: chart.styles?.[key]?.dash ?? "solid",
      endLabel: null,
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
      color: null,
      dash: "solid" as const,
      endLabel: null,
      measure,
    }));
    for (const row of rows) {
      const d = datum(row);
      ys.forEach((j, i) => {
        d[`s${i}`] = toNumber(row[j] ?? null);
      });
    }
  }

  let data = [...byX.values()];
  for (const d of data) {
    for (const s of series) if (!(s.key in d)) d[s.key] = null;
  }
  if (chart.x?.kind === "time") {
    // ISO dates and timestamps sort as text.
    data.sort((a, b) => (a.x < b.x ? -1 : a.x > b.x ? 1 : 0));
  } else if (chart.x?.kind === "number") {
    // Wide integers and decimals arrive as text, which would put lap 10 before
    // lap 9. A row with no number there has no place on the axis; the table
    // view still has it.
    data = data.flatMap((d) => {
      const n = toNumber(d.x);
      return n === null ? [] : [Object.assign(d, { xn: n })];
    });
    data.sort((a, b) => a.xn! - b.xn!);
  }
  const partial = new Set(chart.x?.kind === "time" ? chart.x.partial : []);
  if (chart.type === "line" && partial.size > 0) {
    const cut = data.map((d) => partial.has(d.x));
    data.forEach((d, i) => {
      const near = cut[i] || cut[i - 1] === true || cut[i + 1] === true;
      for (const s of series) {
        d[`${s.key}Whole`] = cut[i] ? null : d[s.key];
        d[`${s.key}Partial`] = near ? d[s.key] : null;
      }
    });
  }
  return { data, series, partial, marks: marked(chart, columns, rows, series) };
}

/**
 * The points each marker layer marks, by series and x, and each series' end
 * label. A row marks the series it belongs to, or, with no series column,
 * every line at its x.
 */
function marked(
  chart: ChartSpec,
  columns: RunColumn[],
  rows: Cell[][],
  series: ChartSeries[],
): Map<string, Map<string, Mark[]>> {
  const marks = new Map<string, Map<string, Mark[]>>();
  const markers = chart.markers ?? [];
  if (markers.length === 0) return marks;
  const at = (name: string) => columns.findIndex((c) => c.name === name);
  const x = at(chart.x?.field ?? "");
  const text = (cell: Cell | undefined) =>
    cell === null || cell === undefined ? "" : String(cell);
  const split = chart.series ? at(chart.series.field) : -1;
  const keyOf = new Map(chart.series?.keys.map((k, i) => [k, series[i].key]));
  const owners = (row: Cell[]) => {
    if (split < 0) return series.map((s) => s.key);
    const key = keyOf.get(text(row[split]));
    return key === undefined ? [] : [key];
  };

  markers.forEach((marker) => {
    if (marker.type !== "end") return;
    const label = at(marker.label);
    for (const row of rows) {
      for (const key of owners(row)) {
        const s = series.find((one) => one.key === key)!;
        if (s.endLabel === null && text(row[label]) !== "") {
          s.endLabel = text(row[label]);
        }
      }
    }
  });

  markers
    .filter((m) => m.type === "point")
    .forEach((marker, layer) => {
      const when = at(marker.when);
      const size = marker.size ? at(marker.size) : -1;
      const hits = rows.filter((row) => row[when] === true);
      const sizes = hits
        .map((row) => (size < 0 ? null : toNumber(row[size] ?? null)))
        .filter((n): n is number => n !== null && Number.isFinite(n));
      const [lo, hi] = [Math.min(...sizes), Math.max(...sizes)];
      for (const row of hits) {
        const n = size < 0 ? null : toNumber(row[size] ?? null);
        const mark: Mark = {
          layer,
          label: marker.label,
          r: n === null || !(hi > lo) ? 4 : 3 + (3 * (n - lo)) / (hi - lo),
          details: (marker.tooltip ?? []).map((name) => ({
            name,
            value: row[at(name)] ?? null,
          })),
        };
        for (const key of owners(row)) {
          let byX = marks.get(key);
          if (!byX) marks.set(key, (byX = new Map()));
          const here = byX.get(text(row[x]));
          if (here) here.push(mark);
          else byX.set(text(row[x]), [mark]);
        }
      }
    });
  return marks;
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
