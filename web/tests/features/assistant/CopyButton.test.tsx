import { describe, it, expect, vi, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CopyButton } from "@/features/assistant/CopyButton";

describe("CopyButton", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("puts the text on the clipboard and says it did", async () => {
    const user = userEvent.setup();
    render(<CopyButton text="SELECT 1;" label="Copy SQL" />);

    await user.click(screen.getByRole("button", { name: "Copy SQL" }));

    expect(await navigator.clipboard.readText()).toBe("SELECT 1;");
    expect(screen.getByRole("status")).toHaveTextContent("Copied");
  });

  it("says so when the browser will not give the page the clipboard", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(
      new Error("denied"),
    );
    render(<CopyButton text="x" label="Copy answer" />);

    await user.click(screen.getByRole("button", { name: "Copy answer" }));

    expect(screen.getByRole("status")).toHaveTextContent("Couldn't copy");
  });

  it("goes back to idle after a moment", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<CopyButton text="x" label="Copy code" />);

    await user.click(screen.getByRole("button", { name: "Copy code" }));
    expect(screen.getByRole("status")).toHaveTextContent("Copied");
    act(() => vi.advanceTimersByTime(2100));

    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("keeps its name while the outcome is announced separately", async () => {
    const user = userEvent.setup();
    render(<CopyButton text="x" label="Copy code" />);

    await user.click(screen.getByRole("button", { name: "Copy code" }));

    expect(
      screen.getByRole("button", { name: "Copy code" }),
    ).toBeInTheDocument();
  });
});
