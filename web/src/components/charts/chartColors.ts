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
 *
 * Every slot clears 3:1 against the surface in both modes, so no mark depends on
 * a label to be seen. Aqua, yellow and magenta (slots 2–4) take a darker step in
 * light mode for that: the brighter steps measured 2.69, 2.07 and 2.57 on the
 * light surface. Each keeps its hue, so a series reads as the same colour in both
 * modes. Worst adjacent pair: ΔE 9.0 under CVD and 17.4 unsimulated in light
 * mode, 8.4 and 19.3 in dark.
 */
export const SERIES: ChartColor[] = [
  { light: "#2a78d6", dark: "#3987e5" },
  { light: "#eb6834", dark: "#d95926" },
  { light: "#05a26f", dark: "#199e70" },
  { light: "#bd7f03", dark: "#c98500" },
  { light: "#d76c95", dark: "#d55181" },
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
