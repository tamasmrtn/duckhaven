import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
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
