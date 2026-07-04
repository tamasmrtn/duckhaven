import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "@tests/utils";

const ROUTE = "/acme-analytics/admin/catalog-access";

describe("CatalogAccessPage", () => {
  it("lists the workspace catalogs with an access-mode control", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    expect(
      await screen.findByRole("heading", { name: "Catalog access" }),
    ).toBeInTheDocument();
    // The access-mode column header and at least one catalog row's selector,
    // shown once the catalog list resolves.
    expect(await screen.findByText("Access mode")).toBeInTheDocument();
    expect(screen.getAllByRole("combobox").length).toBeGreaterThan(0);
  });
});
