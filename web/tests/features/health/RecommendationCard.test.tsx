import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RecommendationCard } from "@/features/health/RecommendationCard";
import { RECOMMENDATIONS } from "@/mock/fixtures/maintenance";

const REC = RECOMMENDATIONS[0];

describe("RecommendationCard", () => {
  it("renders the kind, severity, confidence and rationale", () => {
    render(<RecommendationCard rec={REC} />);
    expect(screen.getByText("Compact small files")).toBeInTheDocument();
    expect(screen.getByText("Critical")).toBeInTheDocument();
    expect(screen.getByText(/high confidence/i)).toBeInTheDocument();
    expect(screen.getByText(REC.rationale)).toBeInTheDocument();
  });

  it("shows the remediation command and says DuckHaven can run it", () => {
    render(<RecommendationCard rec={REC} />);
    expect(screen.getByText(REC.remediation!.command!)).toBeInTheDocument();
    expect(
      screen.getByText(/DuckHaven can run this for you/i),
    ).toBeInTheDocument();
  });

  it("says so plainly when the catalog kind has no verbs to run", () => {
    // Iceberg: DuckDB cannot run these, so the footer names an external engine.
    const rec = {
      ...REC,
      remediation: { ...REC.remediation!, applicable_in_app: false },
    };
    render(<RecommendationCard rec={rec} />);
    expect(
      screen.getByText(/cannot apply this kind of maintenance/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /apply/i })).toBeNull();
  });

  it("offers Apply only when the kind can be applied", () => {
    const onApply = vi.fn();
    render(<RecommendationCard rec={REC} onApply={onApply} />);
    fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));
    expect(onApply).toHaveBeenCalledWith(REC.id);
  });

  it("confirms before a verb that acts on the whole catalog", () => {
    // A catalog-scoped verb states its blast radius before the click.
    const onApply = vi.fn();
    const rec = {
      ...REC,
      remediation: {
        ...REC.remediation!,
        applicable_in_app: true,
        scope: "catalog" as const,
      },
    };
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<RecommendationCard rec={rec} onApply={onApply} />);
    fireEvent.click(screen.getByRole("button", { name: /^apply$/i }));

    expect(confirm).toHaveBeenCalled();
    expect(String(confirm.mock.calls[0][0])).toMatch(
      /every table in this catalog/i,
    );
    expect(onApply).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("reports a failed apply rather than staying silent", () => {
    const rec = {
      ...REC,
      apply_status: "failed" as const,
      apply_error: "no agent",
    };
    render(<RecommendationCard rec={rec} />);
    expect(
      screen.getByText(/Last apply failed: no agent/i),
    ).toBeInTheDocument();
  });

  it("shows the table name when showTable is set", () => {
    render(<RecommendationCard rec={REC} showTable />);
    expect(screen.getByText("analytics.events")).toBeInTheDocument();
  });

  it("calls onDismiss with the recommendation id when dismissed", () => {
    const onDismiss = vi.fn();
    render(<RecommendationCard rec={REC} onDismiss={onDismiss} />);
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(onDismiss).toHaveBeenCalledWith(REC.id);
  });

  it("omits the dismiss button when no handler is provided", () => {
    render(<RecommendationCard rec={REC} />);
    expect(
      screen.queryByRole("button", { name: /dismiss/i }),
    ).not.toBeInTheDocument();
  });
});
