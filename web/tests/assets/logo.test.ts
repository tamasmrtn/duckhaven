import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

// Regression guard: the brand logos were originally imported with ~50% empty
// background padding baked into the canvas and no viewBox, which made the mark
// render tiny everywhere it was used (TopBar, login, etc.). These assertions
// fail if an un-cropped export is reintroduced.
const logos = ["logo-light.svg", "logo-dark.svg"];

describe.each(logos)("brand asset %s", (file) => {
  const svg = readFileSync(resolve("src/assets", file), "utf8");

  it("declares a viewBox so it can be sized/cropped via CSS", () => {
    expect(svg).toMatch(/<svg[^>]*\sviewBox="[^"]+"/);
  });

  it("has no full-canvas background rectangle (renders transparent)", () => {
    // The original padded export filled the whole canvas with these colors.
    expect(svg).not.toContain('fill="#F8FAF9"');
    expect(svg).not.toContain('fill="#0E1520"');
  });
});
