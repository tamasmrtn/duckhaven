// The colour tokens as the stylesheet declares them, and how far apart two of them
// are, for tests that check what a component's classes will look like.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// The source tree, found from this file rather than from wherever the tests were
// started, so the checks read the same files whichever directory runs them.
export const SRC = join(
  dirname(fileURLToPath(import.meta.url)),
  "../../../src",
);

const tokens = readFileSync(join(SRC, "styles/tokens.css"), "utf8");

function block(selector: string): Record<string, string> {
  const found = new RegExp(`${selector}\\s*\\{([^}]*)\\}`).exec(tokens);
  if (!found) throw new Error(`no ${selector} block in tokens.css`);
  return Object.fromEntries(
    [...found[1].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map((m) => [
      m[1],
      m[2].trim(),
    ]),
  );
}

/** What each token resolves to in a theme. Dark overrides what it redeclares. */
export function theme(mode: "light" | "dark"): Record<string, string> {
  return mode === "light"
    ? block(":root")
    : { ...block(":root"), ...block("\\.dark") };
}

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}

/** The WCAG contrast ratio between two hex colours. */
export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
