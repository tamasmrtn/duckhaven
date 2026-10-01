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
 * Two families, because the charts do two different jobs:
 *
 * - `ACTIVITY` (and `TIMELINE`, keyed by the API's states) — how busy the agent
 *   was. Deliberately a single-hue ramp and not separate hues: idle → busy is an
 *   *ordered* scale, so the order belongs in the lightness, where a reader sees it
 *   without consulting the legend.
 *
 * - `SERIES` — nominal identity, the validated eight-slot categorical order,
 *   assigned by fixed index and never cycled. `LOAD`, `OUTCOME` and `RESOURCE`
 *   are named picks from it, each validated in the order the chart stacks them.
 */

export interface ChartColor {
  light: string;
  dark: string;
}

/**
 * Activity intensity, as one blue ramp.
 *
 * The anchor flips between modes — the busiest step is the darkest on a light
 * surface and the brightest on a dark one — so "more intense" always means
 * "further from the background", whichever background you are on.
 */
export const ACTIVITY = {
  query: { light: "#17439e", dark: "#a5c9fb" },
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
 * Where an agent's time went — the timeline strip. The activity ramp above, keyed
 * by the exact states the API now reports (busy is the darkest/brightest step:
 * the one this strip exists to show).
 */
export const TIMELINE = {
  busy: ACTIVITY.query,
  idle: ACTIVITY.ready,
  starting: ACTIVITY.starting,
  down: ACTIVITY.down,
  unknown: ACTIVITY.unknown,
} satisfies Record<string, ChartColor>;

/**
 * Average concurrency by state, stacked. Identity rather than status (these are
 * shares of one quantity), so fixed categorical slots: 0 running, 3 waiting to
 * run, 6 waiting for compute. Validated in stack order — the adjacent pairs are
 * the ones a reader compares — light and dark, all checks passing; amber sits
 * under 3:1 on the light surface, which the legend's values and the query table
 * give the required text relief for.
 */
export const LOAD = {
  running: SERIES[0],
  queued: SERIES[3],
  compute: SERIES[6],
} satisfies Record<string, ChartColor>;

/**
 * Finished queries by outcome, stacked failures-first so the bars an operator
 * acts on sit on the baseline where they read most accurately. The order also
 * keeps red off amber, which failed the normal-vision floor (ΔE 13) in dark.
 * Validated in that stack order, light and dark.
 */
export const OUTCOME = {
  failed: SERIES[7],
  cancelled: SERIES[6],
  sql_error: SERIES[3],
  done: SERIES[0],
} satisfies Record<string, ChartColor>;

/**
 * CPU and memory each get a panel of their own, so each is a single series; they
 * still take distinct slots, unused by the stacked panels, so a hue on this page
 * never means two different things side by side.
 */
export const RESOURCE = {
  cpu: SERIES[2],
  memory: SERIES[4],
} satisfies Record<string, ChartColor>;

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
