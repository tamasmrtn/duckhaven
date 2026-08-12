import { describe, it, expect } from "vitest";
import {
  computeHunks,
  applyHunkResolutions,
  type DiffHunk,
} from "@/features/worksheet/diffHunks";

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
      status: "pending",
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

  it("every hunk starts pending", () => {
    const hunks = computeHunks("a\nb", "a\nc");
    expect(hunks.every((h) => h.status === "pending")).toBe(true);
  });
});

describe("applyHunkResolutions", () => {
  const oldSql = "line1\nline2\nline3\nline4\nline5";
  const newSql = "line1\nCHANGED\nline3\nline4\nCHANGED5";

  function accept(hunks: DiffHunk[]): DiffHunk[] {
    return hunks.map((h) => ({ ...h, status: "accepted" }));
  }
  function reject(hunks: DiffHunk[]): DiffHunk[] {
    return hunks.map((h) => ({ ...h, status: "rejected" }));
  }

  it("reproduces newSql when every hunk is accepted", () => {
    const hunks = accept(computeHunks(oldSql, newSql));
    expect(applyHunkResolutions(oldSql, newSql, hunks)).toBe(newSql);
  });

  it("reproduces newSql when every hunk is still pending", () => {
    const hunks = computeHunks(oldSql, newSql);
    expect(applyHunkResolutions(oldSql, newSql, hunks)).toBe(newSql);
  });

  it("reproduces oldSql when every hunk is rejected", () => {
    const hunks = reject(computeHunks(oldSql, newSql));
    expect(applyHunkResolutions(oldSql, newSql, hunks)).toBe(oldSql);
  });

  it("splices a mixed accept/reject resolution correctly", () => {
    const hunks = computeHunks(oldSql, newSql);
    hunks[0].status = "accepted"; // keep "CHANGED"
    hunks[1].status = "rejected"; // revert to "line5"
    expect(applyHunkResolutions(oldSql, newSql, hunks)).toBe(
      "line1\nCHANGED\nline3\nline4\nline5",
    );
  });

  it("round-trips a pure-insertion accept/reject", () => {
    const o = "SELECT 1;\nSELECT 3;";
    const n = "SELECT 1;\nSELECT 2;\nSELECT 3;";
    const hunks = computeHunks(o, n);
    expect(applyHunkResolutions(o, n, accept(hunks))).toBe(n);
    expect(applyHunkResolutions(o, n, reject(hunks))).toBe(o);
  });

  it("round-trips a pure-deletion accept/reject", () => {
    const o = "SELECT 1;\nSELECT 2;\nSELECT 3;";
    const n = "SELECT 1;\nSELECT 3;";
    const hunks = computeHunks(o, n);
    expect(applyHunkResolutions(o, n, accept(hunks))).toBe(n);
    expect(applyHunkResolutions(o, n, reject(hunks))).toBe(o);
  });

  it("is a no-op with no hunks (identical documents)", () => {
    const sql = "SELECT 1;";
    expect(applyHunkResolutions(sql, sql, [])).toBe(sql);
  });
});
