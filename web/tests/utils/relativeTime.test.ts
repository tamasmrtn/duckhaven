import { describe, it, expect } from "vitest";
import { relativeTime } from "@/utils/relativeTime";

const NOW = Date.parse("2026-09-25T12:00:00Z");
const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();

describe("relativeTime", () => {
  it("counts seconds, minutes and hours", () => {
    expect(relativeTime(ago(2), NOW)).toBe("just now");
    expect(relativeTime(ago(42), NOW)).toBe("42s ago");
    expect(relativeTime(ago(5 * 60), NOW)).toBe("5m ago");
    expect(relativeTime(ago(3 * 3600), NOW)).toBe("3h ago");
  });

  it("rolls over to days instead of hundreds of hours", () => {
    // An agent that last pinged 294 hours ago read "294h ago".
    expect(relativeTime(ago(294 * 3600), NOW)).toBe("12d ago");
  });

  it("gives a date past a month", () => {
    expect(relativeTime(ago(40 * 86400), NOW)).toBe(
      new Date(NOW - 40 * 86400 * 1000).toLocaleDateString(),
    );
  });

  it("shows a dash for no timestamp", () => {
    expect(relativeTime(null, NOW)).toBe("—");
  });
});
