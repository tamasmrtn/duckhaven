import { describe, expect, it } from "vitest";
import {
  formatTableSize,
  isInlined,
  totalTableSize,
} from "@/features/catalog/tableSize";
import type { CatalogTable } from "@/types/catalog";

const table = (fields: Partial<CatalogTable>) =>
  ({
    name: "t",
    format: "ICEBERG",
    row_count: null,
    row_count_estimate: null,
    size_bytes: null,
    ...fields,
  }) as CatalogTable;

describe("isInlined", () => {
  it("flags a DuckLake table with rows but no data-file bytes", () => {
    expect(isInlined(table({ format: "DUCKLAKE", row_count: 5, size_bytes: 0 }))).toBe(true);
    expect(
      isInlined(table({ format: "DUCKLAKE", row_count_estimate: 5, size_bytes: 0 })),
    ).toBe(true);
  });

  it("leaves empty, file-backed and non-DuckLake tables alone", () => {
    expect(isInlined(table({ format: "DUCKLAKE", row_count: 0, size_bytes: 0 }))).toBe(false);
    expect(isInlined(table({ format: "DUCKLAKE", row_count: 5, size_bytes: 2554 }))).toBe(false);
    expect(isInlined(table({ format: "ICEBERG", row_count: 5, size_bytes: 0 }))).toBe(false);
  });
});

describe("formatTableSize", () => {
  it("names inlined tables, formats known sizes and marks unknown ones", () => {
    expect(formatTableSize(table({ format: "DUCKLAKE", row_count: 5, size_bytes: 0 }))).toBe(
      "Inlined",
    );
    expect(formatTableSize(table({ size_bytes: 2554 }))).toBe("2.5 KB");
    expect(formatTableSize(table({ size_bytes: null }))).toBe("—");
  });
});

describe("totalTableSize", () => {
  // Regression: unknown sizes were added up as 0, so a schema of Iceberg
  // tables holding 86M rows read "0 KB".
  it("is unknown, not zero, when no table's size is known", () => {
    expect(totalTableSize([table({}), table({})])).toEqual({
      bytes: null,
      known: 0,
      count: 2,
    });
  });

  it("sums the known sizes and says how many were counted", () => {
    expect(
      totalTableSize([table({ size_bytes: 100 }), table({ size_bytes: 0 }), table({})]),
    ).toEqual({ bytes: 100, known: 2, count: 3 });
  });

  it("is a true zero for no tables at all", () => {
    expect(totalTableSize([])).toEqual({ bytes: 0, known: 0, count: 0 });
  });
});
