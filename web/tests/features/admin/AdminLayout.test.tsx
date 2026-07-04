import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@tests/mock/server";
import { renderWithProviders } from "@tests/utils";
import { CURRENT_USER } from "@/mock/fixtures/users";

const ADMIN_ROUTE = "/acme-analytics/admin/catalog-access";

describe("AdminLayout access gate", () => {
  it("blocks a non-admin who reaches an admin URL directly", async () => {
    // A regular user carries no global permissions.
    server.use(
      http.get("/api/me", () =>
        HttpResponse.json({ ...CURRENT_USER, role: "user", permissions: [] }),
      ),
    );
    renderWithProviders({ initialRoute: ADMIN_ROUTE });

    expect(
      await screen.findByText("Admin access required"),
    ).toBeInTheDocument();
    // The admin shell (its section tabs) must not render.
    expect(
      screen.queryByRole("button", { name: "Agents" }),
    ).not.toBeInTheDocument();
  });

  it("renders the admin shell for an admin", async () => {
    // Default mocked user is an admin (has permissions).
    renderWithProviders({ initialRoute: ADMIN_ROUTE });

    expect(
      await screen.findByRole("button", { name: "Catalog access" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Admin access required")).not.toBeInTheDocument();
  });
});
