import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChartFrame } from "@/features/admin/monitoring/ChartFrame";

describe("ChartFrame", () => {
  it("names the chart and points at its description", () => {
    render(
      <ChartFrame
        title="Queries"
        subtitle="Finished per 5-minute bucket."
        testId="chart-x"
        legend={<span>Succeeded 4</span>}
      >
        <svg data-testid="plot" />
      </ChartFrame>,
    );
    const chart = screen.getByRole("img", { name: "Queries" });
    expect(chart).toHaveAttribute("data-testid", "chart-x");
    const describedBy = chart.getAttribute("aria-describedby")!;
    expect(document.getElementById(describedBy)).toHaveTextContent(
      "Finished per 5-minute bucket.",
    );
    expect(screen.getByTestId("plot")).toBeInTheDocument();
  });

  it("says there is nothing to plot, instead of drawing an axis and a legend of zeros", () => {
    render(
      <ChartFrame
        title="Queries"
        testId="chart-x"
        legend={<span>Succeeded 0</span>}
        emptyMessage="No queries finished in this range."
      >
        <svg data-testid="plot" />
      </ChartFrame>,
    );
    expect(
      screen.getByText("No queries finished in this range."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("plot")).not.toBeInTheDocument();
    expect(screen.queryByText("Succeeded 0")).not.toBeInTheDocument();
  });
});
