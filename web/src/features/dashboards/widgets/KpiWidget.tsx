import { ArrowDown, ArrowUp, Minus } from "lucide-react";
import { cn } from "@/utils";
import type { RunWidget } from "@/types/dashboard";
import { formatChange, formatValue, toNumber } from "../format";

const TONE = {
  good: "text-[var(--status-success)]",
  bad: "text-[var(--status-failed)]",
  neutral: "text-text-secondary",
};

/**
 * One headline number, with how it moved against the period it is compared
 * with. The change is coloured by whether it is good news for this metric, as
 * its polarity says, and always carries an arrow and words as well.
 */
export function KpiWidget({ event }: { event: RunWidget }) {
  const rows = event.rows!;
  const measure = event.chart?.y[0];
  const at = rows.columns.findIndex((c) => c.name === measure?.field);
  const column = rows.columns[at];
  const value = at >= 0 ? (rows.data[0]?.[at] ?? null) : null;
  const compare = event.compare;
  const delta = compare ? toNumber(compare.delta) : null;
  const tone =
    compare?.good === true
      ? "good"
      : compare?.good === false
        ? "bad"
        : "neutral";
  const Arrow =
    delta == null || delta === 0 ? Minus : delta > 0 ? ArrowUp : ArrowDown;
  const direction =
    delta == null || delta === 0 ? "No change" : delta > 0 ? "Up" : "Down";
  const verdict = tone === "good" ? ", good" : tone === "bad" ? ", bad" : "";

  return (
    <div className="flex flex-col gap-1">
      <p
        className="text-2xl font-semibold leading-tight text-text-primary"
        title={formatValue(value, column)}
        data-testid="kpi-value"
      >
        {formatValue(value, column, { compact: true })}
      </p>
      {compare && (
        <p
          className={cn(
            // Positioned, so the screen-reader words stay inside the card
            // rather than stretching the page past a scrolled container.
            "relative flex items-center gap-1 text-xs font-medium",
            TONE[tone],
          )}
          data-testid="kpi-delta"
        >
          <Arrow className="size-3.5" aria-hidden />
          <span className="sr-only">
            {direction}
            {verdict}:
          </span>
          {compare.delta_pct != null && (
            <span>{formatChange(compare.delta_pct)}</span>
          )}
          {delta != null && (
            <span
              className={compare.delta_pct != null ? "text-text-tertiary" : ""}
            >
              {compare.delta_pct != null ? "(" : ""}
              {formatValue(compare.delta, column, {
                compact: true,
                signed: true,
              })}
              {compare.delta_pct != null ? ")" : ""}
            </span>
          )}
          {delta == null && <span>No earlier value to compare with</span>}
        </p>
      )}
      <p className="text-2xs text-text-tertiary">
        {event.scope.window?.text ?? "All time"}
      </p>
      {compare?.window_text && (
        <p className="-mt-1 text-2xs text-text-tertiary">
          Compared with {lowerFirst(compare.window_text)}
        </p>
      )}
    </div>
  );
}

function lowerFirst(text: string): string {
  return text.charAt(0).toLowerCase() + text.slice(1);
}
