// Time formatting for the live utilization charts. The x-axis shows terse
// "time ago" labels relative to the latest sample; the tooltip carries the full
// absolute timestamp so no precision is lost.

export function formatRelativeTick(sampledAt: string, refMs: number): string {
  const diff = (refMs - Date.parse(sampledAt)) / 1000;
  if (diff < 1) return "now";
  if (diff < 60) return `-${Math.round(diff)}s`;
  return `-${Math.round(diff / 60)}m`;
}

export function formatAbsoluteTimestamp(sampledAt: string): string {
  return new Date(sampledAt).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  });
}
