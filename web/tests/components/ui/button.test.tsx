import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Button } from "@/components/ui/button";

describe("Button brand variant", () => {
  it("is the brand yellow with black text, the pairing the Run button already uses", () => {
    render(<Button variant="brand">New thing</Button>);
    const button = screen.getByRole("button", { name: "New thing" });
    expect(button).toHaveClass("bg-[var(--brand-yellow)]", "text-black");
  });

  it("leaves the default variant on the primary token", () => {
    render(<Button>Save</Button>);
    expect(screen.getByRole("button", { name: "Save" })).toHaveClass(
      "bg-primary",
    );
  });
});
