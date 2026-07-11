import { describe, it, expect } from "vitest";
import { pickVerb } from "@/features/assistant/statusVerbs";

describe("pickVerb", () => {
  it("cycles through a tool's own pool by tick", () => {
    const a = pickVerb("run_sql", 0);
    const b = pickVerb("run_sql", 1);
    expect(a).not.toBe(b);
    // Deterministic: the same (tool, tick) always picks the same word.
    expect(pickVerb("run_sql", 0)).toBe(a);
  });

  it("never interpolates a table or column name", () => {
    for (let tick = 0; tick < 12; tick++) {
      for (const tool of [
        "run_sql",
        "describe_table",
        "list_tables",
        null,
        "some_unknown_tool",
      ]) {
        const word = pickVerb(tool, tick);
        expect(word).not.toMatch(/`/);
        expect(word.toLowerCase()).not.toContain("profiling");
      }
    }
  });

  it("falls back to the general pool for an unmapped or absent tool", () => {
    expect(() => pickVerb(null, 0)).not.toThrow();
    expect(() => pickVerb("some_unknown_tool", 0)).not.toThrow();
  });

  it("wraps around once the tick exceeds the pool size", () => {
    const first = pickVerb("describe_table", 0);
    // describe_table's pool has 2 entries.
    expect(pickVerb("describe_table", 2)).toBe(first);
  });
});
