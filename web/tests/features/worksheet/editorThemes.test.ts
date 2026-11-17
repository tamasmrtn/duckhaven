import { describe, expect, it } from "vitest";
import type { Monaco } from "@monaco-editor/react";
import { defineEditorThemes } from "@/features/worksheet/editorThemes";

type Theme = { colors: Record<string, string> };

/** The themes as Monaco is given them, by name. */
function themes(): Record<string, Theme> {
  const defined: Record<string, Theme> = {};
  const monaco = {
    editor: {
      defineTheme: (name: string, theme: Theme) => {
        defined[name] = theme;
      },
    },
  } as unknown as Monaco;
  defineEditorThemes(monaco);
  return defined;
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map(
    (i) => parseInt(hex.slice(i, i + 2), 16) / 255,
  );
  const [r, g, b] = channels.map((c) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("editor themes", () => {
  // The dashboard editor's problems list points at line numbers, which
  // shipped at 1.5:1 in light mode and 1.9:1 in dark.
  it.each(["duckhaven-light", "duckhaven-dark"])(
    "keeps %s's line numbers at 4.5:1 on the editor",
    (name) => {
      const { colors } = themes()[name];
      const background = colors["editor.background"];
      for (const key of [
        "editorLineNumber.foreground",
        "editorLineNumber.activeForeground",
      ]) {
        expect(
          contrast(colors[key], background),
          `${key} ${colors[key]} on ${background}`,
        ).toBeGreaterThanOrEqual(4.5);
      }
    },
  );
});
