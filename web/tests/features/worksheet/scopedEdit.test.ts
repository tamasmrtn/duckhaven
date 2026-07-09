import { describe, it, expect } from "vitest";
import { applyScopedEdit } from "@/features/worksheet/scopedEdit";

describe("applyScopedEdit", () => {
  function anchorFor(current: string, text: string) {
    const start = current.indexOf(text);
    return { text, start, end: start + text.length };
  }

  it("splices the replacement into the anchor range when unscoped context is unchanged", () => {
    const current = "SELECT * FROM events WHERE id = 1";
    const anchor = anchorFor(current, "id = 1");
    const result = applyScopedEdit(current, anchor, "id = 2", true);
    expect(result.sql).toBe("SELECT * FROM events WHERE id = 2");
    expect(result.note).toBeUndefined();
  });

  it("falls back to a full replace when the anchor text no longer matches", () => {
    const original = "SELECT * FROM events WHERE id = 1";
    const anchor = anchorFor(original, "id = 1");
    const current = "SELECT * FROM events WHERE id = 999";
    const result = applyScopedEdit(current, anchor, "id = 2", true);
    expect(result.sql).toBe("id = 2");
    expect(result.note).toMatch(/changed since this was requested/);
  });

  it("falls back to a full replace when there is no anchor at all", () => {
    const result = applyScopedEdit("SELECT 1", null, "SELECT 2", true);
    expect(result.sql).toBe("SELECT 2");
    expect(result.note).toMatch(/changed since this was requested/);
  });

  it("applies a plain full replace when the edit isn't scoped", () => {
    const current = "SELECT * FROM events WHERE id = 1";
    const anchor = anchorFor(current, "id = 1");
    const result = applyScopedEdit(
      current,
      anchor,
      "SELECT * FROM events LIMIT 10",
      false,
    );
    expect(result.sql).toBe("SELECT * FROM events LIMIT 10");
    expect(result.note).toBeUndefined();
  });
});
