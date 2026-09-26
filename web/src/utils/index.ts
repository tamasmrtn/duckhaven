import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format a count with a singular/plural noun, e.g. plural(1, "backend") → "1 backend". */
export function plural(n: number, word: string, suffix = "s") {
  return `${n} ${word}${n === 1 ? "" : suffix}`;
}

const BYTE_UNITS = ["KB", "MB", "GB", "TB"];

/**
 * Format a byte count in the largest unit that keeps it at 1 or more:
 * 512 → "512 B", 2554 → "2.5 KB", 2.2e9 → "2.1 GB", 8e12 → "7.3 TB".
 */
export function formatBytes(n: number | null) {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  let value = n / 1024;
  let unit = 0;
  // Step up before the value would round to "1024.0" in the smaller unit.
  while (value >= 1023.95 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${BYTE_UNITS[unit]}`;
}

/** Format a row count with a K/M/B suffix, e.g. 1234567 → "1.2M". */
export function formatRowCount(n: number | null) {
  if (n == null) return "";
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/** Short, legible fallback for a raw UUID when no human-readable name exists. */
export function shortId(id: string | null | undefined) {
  if (!id) return "—";
  return id.length > 8 ? id.slice(0, 8) : id;
}
