import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@tests/utils";

const SCHEMA_DETAIL = "/acme-analytics/catalog/acme_analytics/raw";

describe("SchemaDetail", () => {
  it("shows the overview with a table count and the three tabs", async () => {
    renderWithProviders({ initialRoute: SCHEMA_DETAIL });

    expect(await screen.findByText("Tables")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /overview/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /details/i })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /permissions/i }),
    ).toBeInTheDocument();
  });
});
