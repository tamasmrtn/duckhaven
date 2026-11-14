import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useAutoRefresh } from "@/features/dashboards/present";

const MINUTE = 60_000;

function hidden(value: boolean) {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => (value ? "hidden" : "visible"),
  });
}

describe("useAutoRefresh", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    hidden(false);
  });
  afterEach(() => {
    vi.useRealTimers();
    hidden(false);
  });

  it("refreshes on its interval, and not while the tab is hidden", () => {
    const refresh = vi.fn();
    renderHook(() => useAutoRefresh(5, refresh));

    vi.advanceTimersByTime(5 * MINUTE - 1);
    expect(refresh).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(refresh).toHaveBeenCalledTimes(1);

    hidden(true);
    vi.advanceTimersByTime(5 * MINUTE);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("does nothing when off, and stops when switched off", () => {
    const refresh = vi.fn();
    const { rerender } = renderHook(
      ({ minutes }: { minutes: number | undefined }) =>
        useAutoRefresh(minutes, refresh),
      { initialProps: { minutes: undefined as number | undefined } },
    );
    vi.advanceTimersByTime(60 * MINUTE);
    expect(refresh).not.toHaveBeenCalled();

    rerender({ minutes: 15 });
    vi.advanceTimersByTime(15 * MINUTE);
    rerender({ minutes: undefined });
    vi.advanceTimersByTime(60 * MINUTE);
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});
