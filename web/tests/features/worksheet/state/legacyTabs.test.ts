import { describe, it, expect, vi, afterEach } from "vitest";
import {
  hasLegacyTabs,
  migrateLegacyTabs,
} from "@/features/worksheet/state/legacyTabs";
import type { Worksheet, WorksheetCreate } from "@/types/worksheet";

const KEY = "dh-worksheets-acme";

function stored() {
  return JSON.parse(localStorage.getItem(KEY) ?? "[]");
}

function creator() {
  let n = 0;
  return vi.fn(async (body: WorksheetCreate) => {
    n += 1;
    return { id: `srv-${n}`, ...body } as unknown as Worksheet;
  });
}

describe("migrateLegacyTabs", () => {
  afterEach(() => sessionStorage.clear());

  it("uploads tabs in order and clears the old keys", async () => {
    localStorage.setItem(
      KEY,
      JSON.stringify([
        { id: "tab-1", title: "events.sql", sql: "SELECT 1", dirty: false },
        { id: "tab-2", title: "draft", sql: "SELECT 2", dirty: true },
      ]),
    );
    localStorage.setItem("dh-pending-sql-acme", "{}");
    sessionStorage.setItem("dh-active-tab-acme", "tab-2");
    const create = creator();

    const { created, activeId } = await migrateLegacyTabs("acme", {
      create,
      savedQueryIds: new Set(),
    });

    expect(create.mock.calls.map(([b]) => b.title)).toEqual([
      "events.sql",
      "draft",
    ]);
    expect(created).toHaveLength(2);
    expect(activeId).toBe("srv-2");
    expect(hasLegacyTabs("acme")).toBe(false);
    expect(localStorage.getItem("dh-pending-sql-acme")).toBeNull();
  });

  it("keeps a saved-query link only while that query exists", async () => {
    localStorage.setItem(
      KEY,
      JSON.stringify([
        { id: "a", title: "kept", sql: "SELECT 1", savedQueryId: "sq-1" },
        { id: "b", title: "orphan", sql: "SELECT 2", savedQueryId: "sq-gone" },
      ]),
    );
    const create = creator();

    await migrateLegacyTabs("acme", { create, savedQueryIds: new Set(["sq-1"]) });

    expect(create.mock.calls.map(([b]) => b.saved_query_id)).toEqual([
      "sq-1",
      null,
    ]);
  });

  it("skips the empty placeholder tab", async () => {
    localStorage.setItem(
      KEY,
      JSON.stringify([{ id: "tab-1", title: "untitled", sql: "", dirty: false }]),
    );
    const create = creator();

    await migrateLegacyTabs("acme", { create, savedQueryIds: new Set() });

    expect(create).not.toHaveBeenCalled();
    expect(hasLegacyTabs("acme")).toBe(false);
  });

  it("resumes after a failure without duplicating what went up", async () => {
    localStorage.setItem(
      KEY,
      JSON.stringify([
        { id: "a", title: "one", sql: "SELECT 1" },
        { id: "b", title: "two", sql: "SELECT 2" },
      ]),
    );
    const create = vi
      .fn()
      .mockResolvedValueOnce({ id: "srv-1" })
      .mockRejectedValueOnce(new Error("offline"));

    await expect(
      migrateLegacyTabs("acme", { create, savedQueryIds: new Set() }),
    ).rejects.toThrow("offline");
    expect(stored().map((t: { id: string }) => t.id)).toEqual(["b"]);

    const retry = creator();
    await migrateLegacyTabs("acme", { create: retry, savedQueryIds: new Set() });
    expect(retry.mock.calls.map(([b]) => b.title)).toEqual(["two"]);
  });
});
