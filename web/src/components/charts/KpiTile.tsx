import { cn } from "@/utils";

/**
 * One headline figure on a card: a label, the value, and a line of detail.
 *
 * The detail line is always laid out, empty or not, so a row of tiles keeps its
 * values and details on shared lines whatever each one says.
 */
export function KpiTile({
  label,
  value,
  detail,
  tone,
  word,
  testId,
}: {
  label: string;
  value: string;
  detail?: string;
  // A text-colour class for the value, for a figure that is also a state.
  tone?: string;
  // A status word ("terminated") rather than a measurement: set smaller so a
  // state does not shout at the size of the numbers beside it.
  word?: boolean;
  testId?: string;
}) {
  return (
    <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-4 py-3">
      <p className="text-2xs uppercase tracking-wide text-text-tertiary">
        {label}
      </p>
      <p
        className={cn(
          // One line box for both sizes, so the detail line under every tile
          // sits at the same height whatever the value's type size.
          "mt-1 font-mono font-tabular leading-8",
          word ? "text-md capitalize" : "text-xl",
          tone,
        )}
        data-testid={testId}
      >
        {value}
      </p>
      <p className="mt-0.5 min-h-4 text-2xs text-text-tertiary">
        {detail ?? ""}
      </p>
    </div>
  );
}
