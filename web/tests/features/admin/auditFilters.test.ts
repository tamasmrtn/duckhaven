import { describe, it, expect } from "vitest";
import {
  parseAuditSearch,
  resolveAuditFilters,
} from "@/features/admin/auditFilters";

const NOW = Date.parse("2026-11-17T12:00:00Z");

describe("audit filters", () => {
  it("keeps the values it knows and drops the rest", () => {
    expect(
      parseAuditSearch({
        category: "access",
        outcome: "nope",
        range: "forever",
        actor: "u-1",
        own: "true",
        workspace: "",
      }),
    ).toEqual({
      category: "access",
      actor: "u-1",
      own: true,
      outcome: undefined,
      range: undefined,
      since: undefined,
      until: undefined,
      subject: undefined,
      workspace: undefined,
    });
  });

  it("reads own access from the router's boolean too", () => {
    expect(parseAuditSearch({ own: true }).own).toBe(true);
    expect(parseAuditSearch({ own: false }).own).toBeUndefined();
  });

  it("defaults to the last 30 days of every event", () => {
    const f = resolveAuditFilters({}, NOW);

    expect(f.range).toBe("30d");
    expect(f.since).toBe("2026-10-18T12:00:00.000Z");
    expect(f.until).toBeUndefined();
    expect(f.scopeLabel).toBe("All events · Last 30 days");
    expect(f.hasFilters).toBe(false);
  });

  it("uses the given window only for a custom range", () => {
    const window = {
      since: "2026-11-01T00:00:00.000Z",
      until: "2026-11-02T00:00:00.000Z",
    };

    expect(
      resolveAuditFilters({ range: "custom", ...window }, NOW),
    ).toMatchObject(window);
    expect(resolveAuditFilters({ range: "all", ...window }, NOW)).toMatchObject(
      {
        since: undefined,
        until: undefined,
        hasFilters: true,
      },
    );
  });
});
