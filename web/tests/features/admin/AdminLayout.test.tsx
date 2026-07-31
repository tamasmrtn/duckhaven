import { describe, it, expect } from "vitest";
import { screen, within } from "@testing-library/react";
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

  it("shows only the sections a partial admin holds", async () => {
    server.use(
      http.get("/api/me", () =>
        HttpResponse.json({
          ...CURRENT_USER,
          role: "user",
          permissions: ["users:manage"],
        }),
      ),
    );
    renderWithProviders({ initialRoute: "/acme-analytics/admin/users" });

    expect(
      await screen.findByRole("button", { name: "Users" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Catalog access" }),
    ).not.toBeInTheDocument();
  });

  it("no longer offers Compute — it moved out to the main nav", async () => {
    // A per-agent grantee holds no global permission, so gating their agent's
    // monitoring page behind this shell would have locked them out of it.
    renderWithProviders({ initialRoute: ADMIN_ROUTE });
    await screen.findByRole("button", { name: "Catalog access" });

    // Scoped to the admin tab bar: the left rail legitimately still has a
    // Compute button, which is the whole point of the move.
    const tabs = within(screen.getByRole("navigation", { name: "Admin sections" }));
    expect(tabs.queryByRole("button", { name: "Agents" })).not.toBeInTheDocument();
    expect(tabs.queryByRole("button", { name: "Compute" })).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Compute" }),
    ).toBeInTheDocument();
  });

  it("still blocks a user holding no global permission", async () => {
    server.use(
      http.get("/api/me", () =>
        HttpResponse.json({ ...CURRENT_USER, role: "user", permissions: [] }),
      ),
    );
    renderWithProviders({ initialRoute: ADMIN_ROUTE });

    expect(
      await screen.findByText("Admin access required"),
    ).toBeInTheDocument();
  });
});
