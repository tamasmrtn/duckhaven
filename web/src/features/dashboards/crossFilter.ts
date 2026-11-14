import type { Cell, ChartSpec, RunColumn, WidgetSpec } from "@/types/dashboard";

/** A parameter as far as a click needs it. */
export interface Selectable {
  type: "time_range" | "dimension";
  model?: string;
  dimension?: string;
}

/**
 * The dimension parameter a click on this widget's bars sets, or null.
 *
 * Bars drawn by a dimension can set a dashboard parameter over the same model
 * and dimension. The server never filters a widget by a dimension it breaks
 * down by, so the chart keeps every bar while the rest of the dashboard
 * narrows to the one chosen.
 */
export function crossFilterParameter(
  spec: WidgetSpec,
  chart: ChartSpec | null | undefined,
  parameters: Record<string, Selectable>,
): string | null {
  const query = spec.query;
  if (!query || chart?.type !== "bar" || chart.x?.kind !== "category") {
    return null;
  }
  const field = chart.x.field;
  const match = Object.entries(parameters).find(
    ([, p]) =>
      p.type === "dimension" &&
      p.model === query.model &&
      p.dimension === field,
  );
  return match ? match[0] : null;
}

/**
 * Each category's value as its rows carry it, by the text the chart keys it
 * by, so a click sends 12 rather than "12".
 */
export function categoryValues(
  chart: ChartSpec,
  columns: RunColumn[],
  rows: Cell[][],
): Map<string, Cell> {
  const x = columns.findIndex((c) => c.name === chart.x?.field);
  const out = new Map<string, Cell>();
  if (x < 0) return out;
  for (const row of rows) {
    const cell = row[x] ?? null;
    if (cell !== null) out.set(String(cell), cell);
  }
  return out;
}
