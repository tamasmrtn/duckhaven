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
