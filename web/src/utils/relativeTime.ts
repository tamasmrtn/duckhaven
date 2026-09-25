/**
 * "just now", "42s ago", "5m ago", "3h ago", "12d ago", then a date.
 *
 * Rolls over to days: an agent that last pinged a week ago reads "7d ago", not
 * "168h ago". Past a month the date says more than a count would.
 */
export function relativeTime(
  iso: string | null | undefined,
  now = Date.now(),
): string {
  if (!iso) return "—";
  const seconds = (now - new Date(iso).getTime()) / 1000;
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  const minutes = seconds / 60;
  if (minutes < 60) return `${Math.floor(minutes)}m ago`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.floor(hours)}h ago`;
  const days = hours / 24;
  if (days < 30) return `${Math.floor(days)}d ago`;
  return new Date(iso).toLocaleDateString();
}
