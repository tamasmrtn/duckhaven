import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// The source tree, found from this file rather than from wherever the tests were
// started, so the check reads the same files whichever directory runs it.
const SRC = join(dirname(fileURLToPath(import.meta.url)), "../../../src");

function files(dir: string, extension: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory()
      ? files(join(dir, entry.name), extension)
      : entry.name.endsWith(extension)
        ? [join(dir, entry.name)]
        : [],
  );
}

const tokens = readFileSync(join(SRC, "styles/tokens.css"), "utf8");
const stylesheets = files(SRC, ".css").map((f) => readFileSync(f, "utf8"));
const components = Object.fromEntries(
  files(join(SRC, "features/assistant"), ".tsx").map((f) => [
    f.slice(SRC.length),
    readFileSync(f, "utf8"),
  ]),
);

function declarations(selector: string): Record<string, string> {
  const block = new RegExp(`${selector}\\s*\\{([^}]*)\\}`).exec(tokens);
  if (!block) throw new Error(`no ${selector} block in tokens.css`);
  return Object.fromEntries(
    [...block[1].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)].map((m) => [
      m[1],
      m[2].trim(),
    ]),
  );
}

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("colours the assistant relies on", () => {
  // WCAG 1.4.3: body text needs 4.5:1. The brand colour on its own reads 4.03:1
  // under white in light mode and 2.80:1 in dark mode, which is why a message
  // from the person uses its own pair of tokens.
  it.each([
    ["light", ":root"],
    ["dark", "\\.dark"],
  ])("the person's message is readable in %s mode", (_mode, selector) => {
    const vars = declarations(selector);

    expect(
      contrast(vars["--text-on-brand"], vars["--bg-brand"]),
    ).toBeGreaterThanOrEqual(4.5);
  });

  it("defines every CSS variable the assistant components use", () => {
    const defined = new Set(
      stylesheets.flatMap((css) =>
        [...css.matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]),
      ),
    );
    const used = Object.entries(components).flatMap(([file, source]) =>
      [...source.matchAll(/var\((--[\w-]+)/g)].map((m) => `${m[1]} (${file})`),
    );

    // A variable nothing defines resolves to nothing, so the style silently does
    // not apply: `--text-muted` was used twice and was never a colour.
    expect(used.filter((entry) => !defined.has(entry.split(" ")[0]))).toEqual(
      [],
    );
  });
});
