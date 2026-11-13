/**
 * The categorical chart palette, shared by every chart in the app.
 *
 * Every value here was produced by running the palette validator (OKLCH lightness
 * band, chroma floor, CVD separation under simulated protanopia/deuteranopia,
 * a normal-vision separation floor, and contrast against the surface) against
 * DuckHaven's own surfaces — `--bg-surface` #f8fafc light, #111827 dark. None of
 * it is eyeballed, and the light and dark columns are separately validated rather
 * than one being an automatic flip of the other.
 *
 * A chart that needs named colours (a state, an outcome) picks slots from
 * `SERIES` and validates them in the order it draws them; see the monitoring
 * page's `chartColors` for examples.
 */

export interface ChartColor {
  light: string;
  dark: string;
}

/**
 * Categorical series identity, in fixed order. Slot N is always the same hue for
 * the same series — never reassigned when a filter changes how many series exist,
 * which would repaint the survivors and silently change what a colour means.
 */
export const SERIES: ChartColor[] = [
  { light: "#2a78d6", dark: "#3987e5" },
  { light: "#eb6834", dark: "#d95926" },
  { light: "#1baf7a", dark: "#199e70" },
  { light: "#eda100", dark: "#c98500" },
  { light: "#e87ba4", dark: "#d55181" },
  { light: "#008300", dark: "#008300" },
  { light: "#4a3aa7", dark: "#9085e9" },
  { light: "#e34948", dark: "#e66767" },
];

/**
 * Resolve a colour for the active theme.
 *
 * Recharts wants a concrete value for `fill`/`stroke` — it cannot take a
 * `var(--token)` for anything it also has to read back (it computes derived
 * colours for active/hover states), so the theme is resolved here instead of in
 * CSS. Pair with `useIsDark`, which re-renders on a theme change.
 */
export function resolve(color: ChartColor, dark: boolean): string {
  return dark ? color.dark : color.light;
}
