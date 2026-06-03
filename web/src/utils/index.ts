import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format a count with a singular/plural noun, e.g. plural(1, "backend") → "1 backend". */
export function plural(n: number, word: string, suffix = "s") {
  return `${n} ${word}${n === 1 ? "" : suffix}`;
}

/** Short, legible fallback for a raw UUID when no human-readable name exists. */
export function shortId(id: string | null | undefined) {
  if (!id) return "—";
  return id.length > 8 ? id.slice(0, 8) : id;
}
