import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { Markdown } from "@/features/assistant/Markdown";

describe("Markdown", () => {
  it("renders a GFM table as a real table", () => {
    render(
      <Markdown>{`| Customer | Orders |\n|---|---:|\n| Alice | 3 |\n| Bob | 2 |`}</Markdown>,
    );
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "Customer" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Alice" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "3" })).toBeInTheDocument();
  });

  it("renders bold, inline code, and lists", () => {
    render(<Markdown>{`**important** and \`code\`\n\n- one\n- two`}</Markdown>);
    expect(screen.getByText("important").tagName).toBe("STRONG");
    expect(screen.getByText("code").tagName).toBe("CODE");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
  });
});

// A reply arriving token by token is re-parsed in full on every frame, so an
// unfinished construct used to render as whatever it currently looked like:
// table header cells as literal pipes in the sentence above them, a bare fence
// marker as an empty code block. The text then jumped into place once the
// construct closed.
const ANSWER = [
  "Here are the **three** biggest tables:",
  "",
  "| Table | Rows |",
  "|---|---:|",
  "| events | 42 |",
  "| users | 7 |",
  "",
  "Then run this:",
  "",
  "```sql",
  "SELECT count(*) FROM events",
  "```",
  "",
  "That should do it.",
].join("\n");

/** The accumulated text after each frame of a chunked stream of `ANSWER`. */
function prefixes(chunkSize: number): string[] {
  const frames: string[] = [];
  for (let end = chunkSize; end < ANSWER.length; end += chunkSize) {
    frames.push(ANSWER.slice(0, end));
  }
  frames.push(ANSWER);
  return frames;
}

/**
 * The rendered text outside code, where markdown control characters are markup
 * rather than content — a code block legitimately contains `count(*)`.
 */
function proseText(container: HTMLElement): string {
  const clone = container.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("pre, code").forEach((el) => el.remove());
  return clone.textContent ?? "";
}

describe("Markdown while streaming", () => {
  it.each([3, 7, 16])(
    "never renders raw markup at %i chars per frame",
    (chunkSize) => {
      // Each frame gets its own mount, so every prefix is asserted on: the
      // throttle starts out holding the value it is first given, so a fresh
      // render shows exactly that frame with no timing to wait on.
      for (const frame of prefixes(chunkSize)) {
        const { container, unmount } = render(
          <Markdown streaming>{frame}</Markdown>,
        );
        expect(
          proseText(container),
          `frame: ${JSON.stringify(frame)}`,
        ).not.toMatch(/[|*`]/);
        unmount();
      }
    },
  );

  it("does not flash an empty code block for a half-typed fence", () => {
    // The frame where the fence marker has arrived but its body has not.
    const partial = ANSWER.slice(0, ANSWER.indexOf("```sql") + 4);
    const { container } = render(<Markdown streaming>{partial}</Markdown>);

    expect(container.querySelector("pre")).toBeNull();
    expect(screen.getByText("Then run this:")).toBeInTheDocument();
  });

  it("still renders the whole answer once the stream finishes", async () => {
    const { container } = render(<Markdown streaming>{ANSWER}</Markdown>);

    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.getByRole("cell", { name: "events" })).toBeInTheDocument();
    expect(container.querySelector("pre")?.textContent).toContain(
      "SELECT count(*) FROM events",
    );
    expect(screen.getByText("three").tagName).toBe("STRONG");
  });

  it("leaves a persisted transcript's markup untouched", () => {
    const streamed = render(<Markdown streaming>{ANSWER}</Markdown>);
    const persisted = render(<Markdown>{ANSWER}</Markdown>);
    expect(persisted.container.innerHTML).toBe(streamed.container.innerHTML);
  });
});
