import { SERIES, type ChartColor } from "@/components/charts/chartColors";

/**
 * Chart colors for the agent monitoring page.
 *
 * Validated the same way as the shared palette, against the same surfaces. Two
 * families, because the charts do two different jobs:
 *
 * - `ACTIVITY` (and `TIMELINE`, keyed by the API's states) — how busy the agent
 *   was. Deliberately a single-hue ramp and not separate hues: idle → busy is an
 *   *ordered* scale, so the order belongs in the lightness, where a reader sees it
 *   without consulting the legend.
 *
 * - `LOAD`, `OUTCOME` and `RESOURCE` — nominal identity, named picks from the
 *   shared categorical `SERIES`, each validated in the order the chart stacks
 *   them.
 */

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
