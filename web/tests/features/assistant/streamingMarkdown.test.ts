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

    it("only rewrites the trailing paragraph", () => {
      expect(stable("Intro para\n\nSecond **par")).toBe(
        "Intro para\n\nSecond par",
      );
    });
  });

  it("returns a finished answer unchanged", () => {
    const answer =
      "Here are the **three** biggest tables:\n\n" +
      "| Table | Rows |\n|---|---:|\n| events | 42 |\n\n" +
      "Then run `SELECT count(*)`.\n";
    expect(stable(answer)).toBe(answer);
  });
});
