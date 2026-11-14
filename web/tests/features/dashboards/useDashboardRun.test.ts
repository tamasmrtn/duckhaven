import { describe, expect, it } from "vitest";
import { reduce, type RunView } from "@/features/dashboards/useDashboardRun";
import type { RunStart, RunWidget } from "@/types/dashboard";

const START: RunStart = {
  type: "start",
  run_id: "r1",
  dashboard: {
    name: "d",
    version: 1,
    spec_hash: "h",
    draft: false,
    preview: null,
  },
  today: "2026-11-13",
  timezone: "UTC",
  parameters: [],
  widgets: ["a", "b"],
};

const answer = (id: string): RunWidget => ({
  type: "widget",
  id,
  state: "ok",
  scope: { window: null, compare_window: null, applied: [], skipped: [] },
});

function after(...actions: Parameters<typeof reduce>[1][]): RunView {
  const empty: RunView = {
    running: false,
    start: null,
    widgets: {},
    waiting: [],
    done: null,
    error: null,
  };
  return actions.reduce(reduce, empty);
}

describe("a run's view", () => {
  it("holds each widget pending until its answer arrives", () => {
    const view = after(
      { kind: "begin" },
      { kind: "event", event: START },
      { kind: "event", event: answer("a") },
    );

    expect(view.running).toBe(true);
    expect(view.widgets.a).toEqual({ event: answer("a"), pending: false });
    expect(view.widgets.b).toEqual({ event: null, pending: true });
  });

  it("keeps the last answers on screen, marked stale, while a new run starts", () => {
    const first = after(
      { kind: "begin" },
      { kind: "event", event: START },
      { kind: "event", event: answer("a") },
    );

    const again = reduce(reduce(first, { kind: "begin" }), {
      kind: "event",
      event: START,
    });

    expect(again.widgets.a).toEqual({ event: answer("a"), pending: true });
  });

  it("knows which widgets wait for compute, until each is answered", () => {
    const view = after(
      { kind: "begin" },
      { kind: "event", event: START },
      {
        kind: "event",
        event: { type: "compute", state: "starting", widgets: ["a", "b"] },
      },
      { kind: "event", event: answer("a") },
    );

    expect(view.waiting).toEqual(["b"]);
  });

  it("says why a run stopped, and stops waiting", () => {
    const failed = after(
      { kind: "begin" },
      { kind: "event", event: START },
      {
        kind: "event",
        event: { type: "error", message: "The dashboard could not be run." },
      },
    );
    const refused = after(
      { kind: "begin" },
      { kind: "fail", message: "No.", code: "invalid_parameters" },
    );

    expect(failed.running).toBe(false);
    expect(failed.error?.message).toBe("The dashboard could not be run.");
    expect(failed.widgets.b.pending).toBe(false);
    expect(refused.error).toEqual({
      message: "No.",
      code: "invalid_parameters",
    });
  });

  it("treats a stream that ends without done as a dropped connection", () => {
    const dropped = after(
      { kind: "begin" },
      { kind: "event", event: START },
      { kind: "closed" },
    );
    const finished = after(
      { kind: "begin" },
      { kind: "event", event: START },
      {
        kind: "event",
        event: {
          type: "done",
          elapsed_ms: 5,
          widgets: {},
          statements: 0,
          cache_hits: 0,
          batch: { used: false, batches: 0 },
        },
      },
      { kind: "closed" },
    );

    expect(dropped.error?.message).toMatch(/connection closed/);
    expect(finished.error).toBeNull();
  });
});
