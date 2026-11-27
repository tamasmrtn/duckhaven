import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { SRC, contrast, theme } from "./colourTokens";

// Every stylesheet and every assistant component, as text, so what the components
// ask of the stylesheets can be checked without rendering anything.
function files(dir: string, extension: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory()
      ? files(join(dir, entry.name), extension)
      : entry.name.endsWith(extension)
        ? [join(dir, entry.name)]
        : [],
  );
}

const stylesheets = files(SRC, ".css").map((f) => readFileSync(f, "utf8"));
const components = Object.fromEntries(
  files(join(SRC, "features/assistant"), ".tsx").map((f) => [
    f.slice(SRC.length),
    readFileSync(f, "utf8"),
  ]),
);

describe("colours the assistant relies on", () => {
  // WCAG 1.4.3: body text needs 4.5:1. The brand colour on its own reads 4.03:1
  // under white in light mode and 2.80:1 in dark mode, which is why a message
  // from the person uses its own pair of tokens.
  it.each(["light", "dark"] as const)(
    "the person's message is readable in %s mode",
    (mode) => {
      const vars = theme(mode);

      expect(
        contrast(vars["--text-on-brand"], vars["--bg-brand"]),
      ).toBeGreaterThanOrEqual(4.5);
    },
  );

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
