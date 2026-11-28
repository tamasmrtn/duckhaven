/**
 * Axis scales a person would pick: steps of 1, 2, 2.5 or 5 times a power of
 * ten, and a range that starts and ends on a step.
 */

const MULTIPLES = [1, 2, 2.5, 5, 10];

/** The round step that cuts `span` into at most about `target` intervals. */
export function niceStep(span: number, target = 4): number {
  if (!(span > 0) || !Number.isFinite(span)) return 1;
  const raw = span / target;
  const power = 10 ** Math.floor(Math.log10(raw));
  const multiple = MULTIPLES.find((m) => m * power >= raw) ?? 10;
  return multiple * power;
}

/** `value` without the float noise a sum of steps picks up: 0.3, not 0.30000000000000004. */
function clean(value: number, step: number): number {
  const decimals = Math.max(0, Math.ceil(-Math.log10(step)) + 1);
  return Number(value.toFixed(decimals)) || 0;
}

/**
 * The range an axis draws to fit values from `min` to `max`, widened to the
 * nearest steps. A flat line gets room either side, and values that are never
 * negative get no negative range.
 */
export function niceDomain(
  min: number,
  max: number,
  target = 4,
): { domain: [number, number]; step: number } {
  const floor = min >= 0 ? 0 : -Infinity;
  if (min === max) {
    const pad = min === 0 ? 1 : Math.abs(min) / 10;
    min -= pad;
    max += pad;
  }
  const step = niceStep(max - min, target);
  const lo = Math.max(floor, Math.floor(min / step) * step);
  const hi = Math.ceil(max / step) * step;
  return { domain: [clean(lo, step), clean(hi, step)], step };
}

/** A tick at every step from one end of `domain` to the other. */
export function stepTicks([lo, hi]: [number, number], step: number): number[] {
  const count = Math.round((hi - lo) / step);
  return Array.from({ length: count + 1 }, (_, i) =>
    clean(lo + i * step, step),
  );
}

/**
 * Round ticks inside `[min, max]`, for an axis drawn edge to edge such as a
 * lap axis: 2, 4, 6 … 12 for laps 1 to 12. `whole` keeps the step at 1 or more.
 */
export function ticksWithin(
  min: number,
  max: number,
  { target = 8, whole = false }: { target?: number; whole?: boolean } = {},
): number[] {
  const step = Math.max(whole ? 1 : 0, niceStep(max - min, target));
  return stepTicks(
    [Math.ceil(min / step) * step, Math.floor(max / step) * step],
    step,
  );
}
