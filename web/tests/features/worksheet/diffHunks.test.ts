import { describe, it, expect } from "vitest";
import { computeHunks } from "@/features/worksheet/diffHunks";

describe("computeHunks", () => {
  it("returns no hunks for identical input", () => {
    const sql = "SELECT * FROM t";
    expect(computeHunks(sql, sql)).toEqual([]);
  });

  it("computes a single replace hunk for a one-line change", () => {
    const oldSql = "SELECT * FROM t ORDER BY x ASC";
    const newSql = "SELECT * FROM t ORDER BY x DESC";
    const hunks = computeHunks(oldSql, newSql);
    expect(hunks).toHaveLength(1);
    expect(hunks[0]).toMatchObject({
      addStartLine: 1,
      addEndLine: 1,
      removedLines: ["SELECT * FROM t ORDER BY x ASC"],
    });
  });

  it("computes a pure insertion hunk", () => {
    const oldSql = "SELECT 1;\nSELECT 3;";
    const newSql = "SELECT 1;\nSELECT 2;\nSELECT 3;";
    const hunks = computeHunks(oldSql, newSql);
    expect(hunks).toHaveLength(1);
    expect(hunks[0]).toMatchObject({
      addStartLine: 2,
      addEndLine: 2,
      removedLines: [],
    });
  });

  it("computes a pure deletion hunk (addStartLine > addEndLine)", () => {
    const oldSql = "SELECT 1;\nSELECT 2;\nSELECT 3;";
    const newSql = "SELECT 1;\nSELECT 3;";
    const hunks = computeHunks(oldSql, newSql);
    expect(hunks).toHaveLength(1);
    expect(hunks[0].removedLines).toEqual(["SELECT 2;"]);
    expect(hunks[0].addStartLine).toBeGreaterThan(hunks[0].addEndLine);
  });

  it("computes multiple disjoint hunks with stable, unique ids", () => {
    const oldSql = "line1\nline2\nline3\nline4\nline5";
    const newSql = "line1\nCHANGED\nline3\nline4\nCHANGED5";
    const hunks = computeHunks(oldSql, newSql);
    expect(hunks).toHaveLength(2);
    expect(new Set(hunks.map((h) => h.id)).size).toBe(2);
    expect(hunks[0].removedLines).toEqual(["line2"]);
    expect(hunks[1].removedLines).toEqual(["line5"]);
  });
});
