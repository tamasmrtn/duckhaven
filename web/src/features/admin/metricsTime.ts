// Time formatting for the live utilization charts. The x-axis is a time scale
// with ticks at clean 1-minute intervals showing terse "time ago" labels
// relative to the latest sample; the tooltip carries the full absolute
// timestamp so no precision is lost. All inputs are epoch milliseconds.

const MINUTE_MS = 60_000;

export function formatRelativeTick(ms: number, refMs: number): string {
  const diff = (refMs - ms) / 1000;
  if (diff < 1) return "now";
  if (diff < 60) return `-${Math.round(diff)}s`;
  return `-${Math.round(diff / 60)}m`;
}

export function formatAbsoluteTimestamp(ms: number): string {
  return new Date(ms).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  });
}

// Evenly-spaced 1-minute tick positions across the window, anchored to the
// latest sample so the rightmost tick is always "now". Returned ascending.
// Anchoring on max keeps labels whole ("-2m", not "-1m58s") and distinct.
export function relativeMinuteTicks(minMs: number, maxMs: number): number[] {
  const ticks: number[] = [];
  for (let t = maxMs; t >= minMs; t -= MINUTE_MS) ticks.push(t);
  return ticks.reverse();
}

// ── Windowed axes (the monitoring page) ──────────────────────────────────────

const HOUR_MS = 3_600_000;

// Clock time, not "time ago". Over a 24-hour window a relative label is the wrong
// unit — "-19h" tells you nothing you can match against a deploy or an incident,
// where "14:20" does. Seconds are dropped: no monitoring bucket is finer than a
// minute, so they would be noise.
export function formatClockTick(ms: number): string {
  return new Date(ms).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

// One tick per round interval, chosen so a window carries 6-12 labels — enough to
// locate a bar in time, few enough not to collide at narrow widths.
export function windowTicks(startMs: number, endMs: number): number[] {
  const span = endMs - startMs;
  const step =
    span <= 1.5 * HOUR_MS
      ? 10 * MINUTE_MS
      : span <= 4 * HOUR_MS
        ? 30 * MINUTE_MS
        : span <= 9 * HOUR_MS
          ? HOUR_MS
          : span <= 13 * HOUR_MS
            ? 2 * HOUR_MS
            : 4 * HOUR_MS;
  // Align to the step so labels land on round times (14:00, not 14:07).
  const ticks: number[] = [];
  for (let t = Math.ceil(startMs / step) * step; t <= endMs; t += step) {
    ticks.push(t);
  }
  return ticks;
}

// Compact wall-clock duration: "6h 12m", "45m", "30s".
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}
