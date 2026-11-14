import type { WidgetSpec } from "@/types/dashboard";

/** A row is 12 columns wide. */
export const GRID_COLUMNS = 12;

/**
 * Each widget's share of its row, in columns, and the columns left empty.
 *
 * Widgets without a width split what the others leave, equally; when every
 * widget has one and they add up to less than 12, the rest of the row stays
 * empty rather than stretching them.
 */
export function rowLayout(widgets: WidgetSpec[]): {
  widths: number[];
  rest: number;
} {
  const fixed = widgets.reduce((sum, w) => sum + (w.width ?? 0), 0);
  const open = widgets.filter((w) => w.width == null).length;
  const share = open ? (GRID_COLUMNS - fixed) / open : 0;
  return {
    widths: widgets.map((w) => w.width ?? share),
    rest: open ? 0 : Math.max(0, GRID_COLUMNS - fixed),
  };
}
