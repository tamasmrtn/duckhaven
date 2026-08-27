import { describe, it, expect } from "vitest";
import { stabilizeStreamingMarkdown } from "@/features/assistant/streamingMarkdown";

const stable = stabilizeStreamingMarkdown;

describe("stabilizeStreamingMarkdown", () => {
  describe("tables", () => {
    it("holds back a table GFM can't build yet", () => {
      expect(stable("Biggest tables:\n\n| Table | Rows |")).toBe(
        "Biggest tables:\n",
      );
    });

    it("holds it back while the delimiter row is still arriving", () => {
      expect(stable("Biggest tables:\n\n| Table | Rows |\n|--")).toBe(
        "Biggest tables:\n",
      );
    });

    it("keeps the table once the delimiter row matches the header", () => {
      const formed = "| Table | Rows |\n|---|---:|\n| events | 4";
      expect(stable(formed)).toBe(formed);
    });
  });

  describe("fenced code", () => {
    it("leaves an unclosed fence alone: its body is already code", () => {
      // CommonMark runs an unclosed fence to the end of the document, so the
      // partial body renders inside <pre> without any help.
      const open = "Try:\n\n```sql\nSELECT 1";
      expect(stable(open)).toBe(open);
    });

    it("holds back a fence marker that is still being typed", () => {
      expect(stable("Try:\n\n```sq")).toBe("Try:\n");
    });

    it("holds back a complete fence marker with no body yet", () => {
      // Otherwise this renders as an empty, padded code block.
      expect(stable("Try:\n\n```sql\n")).toBe("Try:\n");
    });

    it("does not read a fence with an info string as a closing fence", () => {
      // A closing fence carries no info string, so this block is still open and
      // the emphasis pass must not reach into its body.
      const nested = "```\n```sql\nSELECT **x";
      expect(stable(nested)).toBe(nested);
    });

    it("leaves a closed fence alone", () => {
      const closed = "```sql\nSELECT 1\n```";
      expect(stable(closed)).toBe(closed);
    });
  });

  describe("inline markers", () => {
    it("drops an unclosed bold marker but keeps its text", () => {
      expect(stable("The **thr")).toBe("The thr");
    });

    it("leaves closed bold alone", () => {
      const closed = "The **three** biggest";
      expect(stable(closed)).toBe(closed);
    });

    it("drops an unclosed backtick but keeps its text", () => {
      expect(stable("Then run `SELEC")).toBe("Then run SELEC");
    });

    it("does not touch an asterisk inside a complete code span", () => {
      const withStar = "Then run `SELECT count(*)` on it";
      expect(stable(withStar)).toBe(withStar);
    });

    it("leaves snake_case identifiers intact", () => {
      const snake = "the events_table and the order_items table";
      expect(stable(snake)).toBe(snake);
    });

    it("leaves a lone asterisk alone", () => {
      // A single `*` is far more often a bullet or a literal than a half-typed
      // italic, and deleting one mid-stream is worse than the flash it avoids.
      for (const text of [
        "Run SELECT * FROM events",
        "Options:\n\n* First item",
        "* alpha\n* beta\n* gamma",
      ]) {
        expect(stable(text)).toBe(text);
      }
    });

    it("pairs backtick runs by length", () => {
      for (const text of ["``a`b``", "run `SELECT\n1` now"]) {
        expect(stable(text)).toBe(text);
      }
    });

    it("only rewrites the trailing paragraph", () => {
      expect(stable("Intro para\n\nSecond **par")).toBe(
        "Intro para\n\nSecond par",
      );
    });
  });

  it("leaves a table written without outer pipes alone", () => {
    // A known limit: recognising these would mean treating any trailing line
    // with a pipe in it as a table, which would swallow ordinary prose.
    const borderless = "Biggest:\n\nTable | Rows\n---|---";
    expect(stable(borderless)).toBe(borderless);
  });

  it("returns a finished answer unchanged", () => {
    const answer =
      "Here are the **three** biggest tables:\n\n" +
      "| Table | Rows |\n|---|---:|\n| events | 42 |\n\n" +
      "Then run `SELECT count(*)`.\n";
    expect(stable(answer)).toBe(answer);
  });
});
