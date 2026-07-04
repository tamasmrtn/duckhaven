import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@tests/utils";

// The MSW fixtures seed ws-1 (acme-analytics) with a default catalog
// `acme_analytics` holding the `raw` and `analytics` schemas.
const CATALOG_DETAIL = "/acme-analytics/catalog/acme_analytics";

describe("CatalogDetail", () => {
  it("shows the overview with a schema count and the three tabs", async () => {
    renderWithProviders({ initialRoute: CATALOG_DETAIL });

    // Breadcrumb + overview stat.
    expect(await screen.findByText("Schemas")).toBeInTheDocument();
    // The catalog detail pane exposes Overview / Details / Permissions tabs.
    expect(screen.getByRole("tab", { name: /overview/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /details/i })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /permissions/i }),
    ).toBeInTheDocument();
  });
});
