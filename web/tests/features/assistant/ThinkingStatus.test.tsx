import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@tests/utils";
import { ThinkingStatus } from "@/features/assistant/ThinkingStatus";
import { pickVerb } from "@/features/assistant/statusVerbs";

function mockReducedMotion(matches: boolean) {
  vi.spyOn(window, "matchMedia").mockImplementation(
    (query: string) =>
      ({
        matches: query.includes("prefers-reduced-motion") ? matches : false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
}

describe("ThinkingStatus", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockReducedMotion(false);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("rotates the status word roughly every 2s", () => {
    render(<ThinkingStatus currentTool={null} />);
    expect(screen.getByText(pickVerb(null, 0), { exact: false })).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(2000));
    expect(screen.getByText(pickVerb(null, 1), { exact: false })).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(2000));
    expect(screen.getByText(pickVerb(null, 2), { exact: false })).toBeInTheDocument();
  });

  it("shows an elapsed-seconds counter that increments every second", () => {
    render(<ThinkingStatus currentTool="run_sql" />);
    expect(screen.getByText("0s")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByText("3s")).toBeInTheDocument();
  });

  it("freezes the word under prefers-reduced-motion, but keeps the counter moving", () => {
    mockReducedMotion(true);
    render(<ThinkingStatus currentTool={null} />);
    const initialWord = pickVerb(null, 0);
    expect(screen.getByText(initialWord, { exact: false })).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(6000));
    // Still the first word — no rotation — but the timer kept counting.
    expect(screen.getByText(initialWord, { exact: false })).toBeInTheDocument();
    expect(screen.getByText("6s")).toBeInTheDocument();
  });
});
