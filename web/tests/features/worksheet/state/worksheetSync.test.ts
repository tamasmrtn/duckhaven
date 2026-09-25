import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { ApiError } from "@/api/client";
import {
  WorksheetSync,
  type SyncHooks,
} from "@/features/worksheet/state/worksheetSync";
import type { Worksheet } from "@/types/worksheet";

function sheet(over: Partial<Worksheet> = {}): Worksheet {
  return {
    id: "w1",
    workspace_id: "ws-1",
    owner_id: "u-1",
    title: "Untitled",
    sql: "",
    agent_id: null,
    catalog: null,
    timeout_s: null,
    saved_query_id: null,
    last_query_id: null,
    is_open: true,
    tab_position: 0,
    version: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function harness(save?: SyncHooks["save"]) {
  let version = 1;
  const hooks = {
    save: vi.fn(
      save ??
        (async (_id: string, body: { sql?: string; base_version: number }) =>
          sheet({ sql: body.sql ?? "", version: ++version })),
    ),
    saved: vi.fn(),
    status: vi.fn(),
    conflict: vi.fn(),
    deleted: vi.fn(),
  };
  const sync = new WorksheetSync(hooks);
  sync.track("w1", 1);
  return { sync, hooks };
}

describe("WorksheetSync", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("merges a burst of edits into one save after the debounce", async () => {
    const { sync, hooks } = harness();
    sync.edit("w1", { sql: "S" });
    await vi.advanceTimersByTimeAsync(300);
    sync.edit("w1", { sql: "SE" });
    await vi.advanceTimersByTimeAsync(300);
    sync.edit("w1", { sql: "SEL" });
    expect(hooks.save).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(800);

    expect(hooks.save).toHaveBeenCalledTimes(1);
    expect(hooks.save).toHaveBeenCalledWith("w1", {
      sql: "SEL",
      base_version: 1,
    });
  });

  it("saves at least every five seconds while typing never pauses", async () => {
    const { sync, hooks } = harness();
    for (let t = 0; t < 5000; t += 500) {
      sync.edit("w1", { sql: `v${t}` });
      await vi.advanceTimersByTimeAsync(500);
    }
    expect(hooks.save).toHaveBeenCalledTimes(1);
  });

  it("sends edits made during a save next, against the new version", async () => {
    let release: () => void = () => {};
    const { sync, hooks } = harness(
      vi
        .fn()
        .mockImplementationOnce(
          () =>
            new Promise<Worksheet>((resolve) => {
              release = () => resolve(sheet({ sql: "A", version: 2 }));
            }),
        )
        .mockResolvedValueOnce(sheet({ sql: "AB", version: 3 })),
    );
    sync.edit("w1", { sql: "A" });
    const first = sync.flush("w1");
    sync.edit("w1", { sql: "AB" });
    release();
    await first;
    await vi.advanceTimersByTimeAsync(10);

    expect(hooks.save).toHaveBeenCalledTimes(2);
    expect(hooks.save).toHaveBeenLastCalledWith("w1", {
      sql: "AB",
      base_version: 2,
    });
    expect(hooks.saved).toHaveBeenCalledWith("w1", expect.anything(), true);
  });

  it("pauses on a conflict and resumes on the side the user picks", async () => {
    const current = sheet({ sql: "theirs", version: 5 });
    const save = vi
      .fn()
      .mockRejectedValueOnce(
        new ApiError(409, "changed", "worksheet_conflict", { current }),
      )
      .mockResolvedValueOnce(sheet({ sql: "mine", version: 6 }));
    const { sync, hooks } = harness(save);

    sync.edit("w1", { sql: "mine" });
    await sync.flush("w1");
    expect(hooks.conflict).toHaveBeenCalledWith("w1", current);
    expect(sync.statusOf("w1")).toBe("conflict");

    // Further typing does not retry behind the user's back.
    sync.edit("w1", { sql: "mine" });
    await vi.advanceTimersByTimeAsync(10_000);
    expect(save).toHaveBeenCalledTimes(1);

    await sync.keepMine("w1");
    expect(save).toHaveBeenLastCalledWith("w1", {
      sql: "mine",
      base_version: 5,
    });
    expect(sync.statusOf("w1")).toBe("idle");
  });

  it("drops the local edit when the user takes the other version", async () => {
    const current = sheet({ sql: "theirs", version: 5 });
    const save = vi
      .fn()
      .mockRejectedValue(
        new ApiError(409, "changed", "worksheet_conflict", { current }),
      );
    const { sync } = harness(save);
    sync.edit("w1", { sql: "mine" });
    await sync.flush("w1");

    sync.takeTheirs("w1");

    expect(sync.hasPending("w1")).toBe(false);
    expect(sync.statusOf("w1")).toBe("idle");
  });

  it("retries with backoff after a failure", async () => {
    const save = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(503, "down"))
      .mockRejectedValueOnce(new ApiError(503, "down"))
      .mockResolvedValueOnce(sheet({ version: 2 }));
    const { sync, hooks } = harness(save);
    sync.edit("w1", { sql: "x" });
    await sync.flush("w1");
    expect(hooks.status).toHaveBeenLastCalledWith("w1", "error");

    await vi.advanceTimersByTimeAsync(2000);
    expect(save).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(3999);
    expect(save).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(save).toHaveBeenCalledTimes(3);
    expect(sync.statusOf("w1")).toBe("idle");
  });

  it("reports a worksheet deleted elsewhere", async () => {
    const save = vi.fn().mockRejectedValue(new ApiError(404, "gone"));
    const { sync, hooks } = harness(save);
    sync.edit("w1", { sql: "x" });
    await sync.flush("w1");
    expect(hooks.deleted).toHaveBeenCalledWith("w1");
    expect(sync.hasPending("w1")).toBe(false);
  });

  it("hands over small pending edits for an unload, and flags large ones", () => {
    const { sync } = harness();
    sync.track("w2", 4);
    sync.edit("w1", { sql: "small" });
    sync.edit("w2", { sql: "x".repeat(70_000) });

    const { bodies, unsendable } = sync.unloadBodies();

    expect(bodies).toEqual([
      { id: "w1", body: { sql: "small", base_version: 1 } },
    ]);
    expect(unsendable).toBe(true);
  });

  it("does not move the base version under a pending edit", () => {
    const { sync, hooks } = harness();
    sync.edit("w1", { sql: "x" });
    sync.track("w1", 9);
    void sync.flush("w1");
    expect(hooks.save).toHaveBeenCalledWith("w1", { sql: "x", base_version: 1 });
  });
});
