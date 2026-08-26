import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@tests/mock/server";
import { render, createWrapper } from "@tests/utils";
import { TableHealthPanel } from "@/features/health/TableHealthPanel";

function renderPanel(table = "events") {
  const { wrapper } = createWrapper();
  return render(
    <TableHealthPanel
      ws="acme-analytics"
      catalog="acme_analytics"
      schema="analytics"
      table={table}
    />,
    { wrapper },
  );
}

describe("TableHealthPanel", () => {
  it("renders the table score, dimension breakdown and recommendations", async () => {
    renderPanel("events");

    // Score gauge for the table.
    expect(
      await screen.findByRole("img", { name: /health score/i }),
    ).toBeInTheDocument();
    // Per-dimension factor breakdown.
    expect(screen.getByText("Fragmentation")).toBeInTheDocument();
    expect(screen.getByText("Snapshot hygiene")).toBeInTheDocument();
    // This table's own recommendations.
    expect(screen.getByText("Compact small files")).toBeInTheDocument();
  });

  it("shows an empty state when the table has no health sample", async () => {
    server.use(
      http.get(
        "/api/workspaces/:ws/catalogs/:catalog/schemas/:schema/tables/:table/health",
        () =>
          HttpResponse.json(
            {
              error: "not_found",
              message: "No health data yet",
              details: null,
            },
            { status: 404 },
          ),
      ),
    );
    renderPanel("events");
    expect(await screen.findByText(/no health data yet/i)).toBeInTheDocument();
  });
});
