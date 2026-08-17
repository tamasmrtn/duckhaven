import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

/**
 * `--bg-code` stays dark in *both* themes — it is the Monaco editor surface, and
 * `tokens.css` defines the same value under light and dark. `--text-primary`
 * does flip. So any element that paints `--bg-code` and then inherits the
 * ambient text colour renders near-black on near-black in light mode.
 *
 * That shipped once: the semantic layer's SQL preview measured **1.07:1** in the
 * browser, against the 4.5:1 AA floor, and every one of the 600+ component tests
 * passed because they assert on text *content*, which is present and correct —
 * just invisible. Rendering tests cannot see colour, so this reads the source
 * instead and pins the token pairing the rest of the app already follows.
 */

// Vitest runs with `web/` as its root, so this resolves deterministically.
const SRC = resolve(process.cwd(), "src");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.tsx?$/.test(entry) ? [full] : [];
  });
}

/** Every `className` string literal that paints the code surface. */
function codeSurfaceClassNames(): { file: string; value: string }[] {
  const found: { file: string; value: string }[] = [];
  for (const file of sourceFiles(SRC)) {
    const source = readFileSync(file, "utf8");
    // className="..." and className={cn("...", ...)} both surface as quoted runs.
    for (const match of source.matchAll(/"([^"]*bg-\[var\(--bg-code\)\][^"]*)"/g)) {
      found.push({ file: file.slice(SRC.length + 1), value: match[1] });
    }
  }
  return found;
}

describe("code surface contrast", () => {
  it("finds the code surfaces it is meant to be guarding", () => {
    // A regex that silently matches nothing would make every assertion vacuous.
    expect(codeSurfaceClassNames().length).toBeGreaterThan(0);
  });

  it("pairs every --bg-code surface with --text-code", () => {
    const offenders = codeSurfaceClassNames().filter(
      ({ value }) => !value.includes("text-[var(--text-code)]"),
    );

    expect(
      offenders.map((o) => `${o.file}: ${o.value}`),
      "an element painting --bg-code must set --text-code; inheriting the " +
        "ambient colour is near-black on near-black in light mode",
    ).toEqual([]);
  });

  it("keeps --bg-code dark in both themes, which is why the pairing is required", () => {
    const tokens = readFileSync(join(SRC, "styles/tokens.css"), "utf8");
    const values = [...tokens.matchAll(/--bg-code:\s*([^;]+);/g)].map((m) =>
      m[1].trim(),
    );

    expect(values.length).toBeGreaterThanOrEqual(2);
    expect(new Set(values).size).toBe(1);
  });
});
