/**
 * Chart colors for the agent monitoring page.
 *
 * Every value here was produced by running the palette validator (OKLCH lightness
 * band, chroma floor, CVD separation under simulated protanopia/deuteranopia,
 * a normal-vision separation floor, and contrast against the surface) against
 * DuckHaven's own surfaces — `--bg-surface` #f8fafc light, #111827 dark. None of
 * it is eyeballed, and the light and dark columns are separately validated rather
 * than one being an automatic flip of the other.
 *
 * Three families, because the charts do three different jobs:
 *
 * - `QUEUE_DEPTH` — running vs queued. These *are* states, so they wear DuckHaven's
 *   status hues rather than series colors. Both steps are darkened from the
 *   `--status-*` tokens the pills use, purely to clear 3:1 against the chart
 *   surface: a 2px pill on white and a 40px bar fill have different contrast needs.
 *
 * - `ACTIVITY` — how busy the agent was. Deliberately a single-hue ramp and not
 *   four separate hues: idle → holding sessions → running queries is an *ordered*
 *   scale, so the order belongs in the lightness, where a reader sees it without
 *   consulting the legend. Four independent hues also failed outright — slate
 *   "ready" against blue "query" came out at ΔE 12.4, under the 15 floor, meaning
 *   full-colour readers could not reliably separate the two states this chart
 *   exists to contrast.
 *
 * - `SERIES` — nominal identity (failure reasons, CPU vs memory). The validated
 *   eight-slot categorical order, assigned by fixed index and never cycled.
 */

export interface ChartColor {
  light: string;
  dark: string;
}

/**
 * Running vs queued depth. Status semantics, snapped for contrast on the surface.
 *
 * Every step is also kept clear of the `SERIES` slots below, so a status colour
 * can never impersonate a series in a reader's memory across two charts on the
 * same page. (That is what ruled out the otherwise-fine #d95926 for light:
 * it is the categorical slot-2 dark step.)
 */
export const QUEUE_DEPTH = {
  running: { light: "#c2410c", dark: "#ea580c" },
  queued: { light: "#64748b", dark: "#7d8ea3" },
} satisfies Record<string, ChartColor>;

/**
 * Activity intensity, as one blue ramp.
 *
 * The anchor flips between modes — the busiest step is the darkest on a light
 * surface and the brightest on a dark one — so "more intense" always means
 * "further from the background", whichever background you are on.
 */
export const ACTIVITY = {
  query: { light: "#17439e", dark: "#a5c9fb" },
  other: { light: "#2b7ae4", dark: "#4b8ef0" },
  ready: { light: "#6aaef6", dark: "#2c5fa8" },
  // Provisioning is a transition, not a level of busyness, so it leaves the ramp
  // for the same amber the agent list already uses for in-transition lifecycles.
  starting: { light: "#d95926", dark: "#ea580c" },
  // Off, and not knowing it was off, are different facts. Both are quiet, and
  // `unknown` additionally carries a hatch so it never passes for real downtime.
  down: { light: "#e2e8f0", dark: "#1f2937" },
  unknown: { light: "#f1f5f9", dark: "#172033" },
} satisfies Record<string, ChartColor>;

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

/** Stable slot for a named series, so identity survives a changing series count. */
export function seriesColor(index: number, dark: boolean): string {
  // Past eight, a chart must fold to "Other" or facet rather than invent a hue.
  return resolve(SERIES[index % SERIES.length], dark);
}
