import { formatBytes } from "@/utils";
import type { MetricFormat } from "@/types/semantic";
import type { Cell } from "@/types/dashboard";

/** How a value should read: a measure's format from its metric, or nothing. */
export interface ValueFormat {
  format?: MetricFormat | null;
  unit?: string | null;
  currency?: string | null;
}

export interface FormatOptions {
  /**
   * 12.9K rather than 12,890: for a figure read at a glance, such as a KPI.
   * Below 10,000 a number is short enough to read in full.
   */
  compact?: boolean;
  /**
   * A change rather than a value: always signed, and a change in a percent
   * reads in percentage points, since +0.4% of 3.1% would be a different thing.
   */
  signed?: boolean;
}

const COMPACT_FROM = 10_000;

/** A cell as a number, or null when it is not one. DECIMALs arrive as strings. */
export function toNumber(value: Cell): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function duration(seconds: number, signed: boolean): string {
  const sign = seconds < 0 ? "−" : signed && seconds > 0 ? "+" : "";
  const s = Math.abs(seconds);
  let text: string;
  if (s < 1) text = `${Math.round(s * 1000)} ms`;
  else if (s < 60) text = `${s.toFixed(s < 10 ? 1 : 0)} s`;
  else if (s < 3600) text = `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  else text = `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
  return sign + text;
}

/**
 * A value as a reader should see it, following its metric's format: a percent
 * is a ratio (0.25 reads 25%), a duration is in seconds, bytes are bytes. A
 * value that is not a number shows as it came.
 */
export function formatValue(
  value: Cell,
  fmt: ValueFormat = {},
  { compact = false, signed = false }: FormatOptions = {},
): string {
  if (value === null || value === undefined) return "—";
  const n = toNumber(value);
  if (n === null) return String(value);
  const sign = signed ? ("exceptZero" as const) : ("auto" as const);
  compact = compact && Math.abs(n) >= COMPACT_FROM;
  switch (fmt.format) {
    case "percent":
      if (signed) {
        const points = new Intl.NumberFormat(undefined, {
          maximumFractionDigits: 1,
          signDisplay: sign,
        }).format(n * 100);
        return `${points} pts`;
      }
      return new Intl.NumberFormat(undefined, {
        style: "percent",
        maximumFractionDigits: 1,
        signDisplay: sign,
      }).format(n);
    case "currency":
      if (fmt.currency) {
        return new Intl.NumberFormat(undefined, {
          style: "currency",
          currency: fmt.currency,
          notation: compact ? "compact" : "standard",
          maximumFractionDigits: compact ? 1 : 2,
          signDisplay: sign,
        }).format(n);
      }
      break;
    case "duration":
      return duration(n, signed);
    case "bytes": {
      const text = formatBytes(Math.abs(Math.round(n)));
      return n < 0 ? `−${text}` : signed && n > 0 ? `+${text}` : text;
    }
  }
  const text = new Intl.NumberFormat(undefined, {
    notation: compact ? "compact" : "standard",
    maximumFractionDigits: compact ? 1 : 2,
    signDisplay: sign,
  }).format(n);
  return fmt.unit ? `${text} ${fmt.unit}` : text;
}

const MIDNIGHT =
  /^(\d{4}-\d{2}-\d{2})[T ]00:00:00(?:\.0+)?(?:Z|[+-]00(?::?00)?)?$/;

/**
 * A time cell as a reader should see it. A week or a month arrives as the
 * timestamp of its first midnight, which is a day, so it reads as that date.
 */
export function formatTime(value: Cell): string {
  if (value === null || value === undefined) return "—";
  const text = String(value);
  return MIDNIGHT.exec(text)?.[1] ?? text;
}

/** A relative change as a signed percent: 0.125 reads +12.5%. */
export function formatChange(ratio: number): string {
  return new Intl.NumberFormat(undefined, {
    style: "percent",
    maximumFractionDigits: 1,
    signDisplay: "exceptZero",
  }).format(ratio);
}
