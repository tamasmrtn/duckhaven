import { describe, expect, it } from "vitest";
import { formatBytes } from "@/utils";

describe("formatBytes", () => {
  it("says unknown for null", () => {
    expect(formatBytes(null)).toBe("—");
  });

  // Regression: anything under 512 bytes, and an empty table, read "0 KB".
  it("keeps sizes under a kilobyte in bytes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1)).toBe("1 B");
    expect(formatBytes(1023)).toBe("1023 B");
  });

  it("picks the largest unit that keeps the value at 1 or more", () => {
    expect(formatBytes(2554)).toBe("2.5 KB");
    expect(formatBytes(123_794_530)).toBe("118.1 MB");
    expect(formatBytes(2_223_533_507)).toBe("2.1 GB");
    expect(formatBytes(8 * 1024 ** 4)).toBe("8.0 TB");
  });

  it("stays in terabytes above them", () => {
    expect(formatBytes(3 * 1024 ** 5)).toBe("3072.0 TB");
  });

  it("steps up rather than showing 1024.0 of the smaller unit", () => {
    expect(formatBytes(1024 ** 2 - 1)).toBe("1.0 MB");
    expect(formatBytes(1024 ** 3 - 1)).toBe("1.0 GB");
  });
});
