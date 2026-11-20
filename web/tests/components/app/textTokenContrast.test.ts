import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { readdirSync } from "node:fs";
import { join, resolve } from "node:path";

/**
 * `--text-tertiary` carries help text, timestamps, empty-state copy and the
 * search placeholder across ~70 files, mostly at 11-13px. It shipped at 2.5:1
 * in light mode, and no rendering test noticed because they assert on text
 * content, not colour. This reads `tokens.css` and pins the AA floor (4.5:1)
 * for the surfaces the token actually sits on, and for the solid status-pill
 * fills that carry white text.
 */

const tokens = readFileSync(
  join(resolve(process.cwd(), "src"), "styles/tokens.css"),
  "utf8",
);

/** Every file under `dir`, recursively. */
function* sourceFiles(dir: string): Generator<string> {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* sourceFiles(path);
    else yield path;
  }
}

/** Every definition of a custom property, in file order: light first, dark second. */
function values(name: string): string[] {
  return [...tokens.matchAll(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})\\s*;`, "g"))].map(
    (m) => m[1],
  );
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("text token contrast", () => {
  it("defines the token once per theme, so the pairs below are real", () => {
    // A regex that silently matches nothing would make every assertion vacuous.
    expect(values("--text-tertiary")).toHaveLength(2);
    expect(values("--bg-canvas")).toHaveLength(2);
    expect(values("--bg-surface")).toHaveLength(2);
    expect(values("--bg-elevated")).toHaveLength(2);
  });

  it.each([
    ["light", 0],
    ["dark", 1],
  ])("keeps --text-tertiary at 4.5:1 on every %s surface", (_theme, i) => {
    const text = values("--text-tertiary")[i];
    for (const surface of ["--bg-canvas", "--bg-surface", "--bg-elevated"]) {
      const bg = values(surface)[i];
      expect(
        contrast(text, bg),
        `${text} on ${surface} ${bg}`,
      ).toBeGreaterThanOrEqual(4.5);
    }
  });

  // Link text, and the good/bad colours a KPI's change is written in, sit at
  // 12-13px on cards and on the orange-tinted notes. Each shipped below AA.
  it.each([
    ["--link", "light", 0],
    ["--link", "dark", 1],
    ["--status-success", "light", 0],
    ["--status-success", "dark", 1],
    ["--status-failed", "light", 0],
    ["--status-failed", "dark", 1],
    // Column types beside their names, at 11-12px in the catalog tree, a
    // table's schema and the results header. They shipped at 2.4:1 in light
    // mode in the brand maya blue.
    ["--text-type", "light", 0],
    ["--text-type", "dark", 1],
  ])("keeps %s at 4.5:1 on every %s surface", (token, _theme, i) => {
    const defined = values(token);
    expect(defined).toHaveLength(2);
    for (const surface of ["--bg-canvas", "--bg-surface", "--bg-elevated"]) {
      const bg = values(surface)[i];
      expect(
        contrast(defined[i], bg),
        `${token} ${defined[i]} on ${surface} ${bg}`,
      ).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("writes no text in the brand maya blue, which is 2.4:1 in light mode", () => {
    // Icons may use it; text takes --text-type, --link or a text token. The
    // lineage strip and a snapshot's operation are known holdouts.
    const offenders = [...sourceFiles(join(resolve(process.cwd(), "src")))]
      .filter((f) => /\.tsx$/.test(f))
      .flatMap((f) =>
        readFileSync(f, "utf8")
          .split("\n")
          .map((line, n) => [f, n + 1, line] as const),
      )
      .filter(
        ([f, , line]) =>
          /\btext-\[var\(--brand-maya-blue\)\]/.test(line) &&
          // An icon component on the same line: `<Layers className="…" />`.
          !/<[A-Z]\w*\s+className=/.test(line) &&
          !/(LineageGraph|LineagePanel|SnapshotHistoryPanel)\.tsx$/.test(f),
      )
      .map(([f, n]) => `${f}:${n}`);
    expect(offenders).toEqual([]);
  });

  it("keeps --link at 4.5:1 on the light note's orange tint", () => {
    // The Banner is brand orange at 10% over the canvas: #fff0e6 in light mode.
    expect(contrast(values("--link")[0], "#fff0e6")).toBeGreaterThanOrEqual(
      4.5,
    );
  });

  it.each([
    ["light", 0, "#f1f5f9"],
    ["dark", 1, "#1e293b"],
  ])("keeps the %s navigation marker at 3:1 beside the current page", (_theme, i, tint) => {
    // On the navigation's surface, and on the current page's tint.
    const markers = values("--nav-marker");
    expect(markers).toHaveLength(2);
    expect(contrast(markers[i], values("--bg-surface")[i])).toBeGreaterThanOrEqual(3);
    expect(contrast(markers[i], tint)).toBeGreaterThanOrEqual(3);
  });

  it.each(["queued", "running", "success", "failed", "cancelled"])(
    "keeps the %s pill fill at 4.5:1 against its white label",
    (status) => {
      const fills = values(`--pill-${status}`);
      expect(fills).toHaveLength(1);
      expect(contrast("#ffffff", fills[0])).toBeGreaterThanOrEqual(4.5);
    },
  );
});
