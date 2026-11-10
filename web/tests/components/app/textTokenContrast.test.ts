import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
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

  it.each(["queued", "running", "success", "failed", "cancelled"])(
    "keeps the %s pill fill at 4.5:1 against its white label",
    (status) => {
      const fills = values(`--pill-${status}`);
      expect(fills).toHaveLength(1);
      expect(contrast("#ffffff", fills[0])).toBeGreaterThanOrEqual(4.5);
    },
  );
});
