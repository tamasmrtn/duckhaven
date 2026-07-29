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

  it("admits a per-agent grantee to the Agents section alone", async () => {
    // A grant is not a global permission: it entitles its holder to the agent's
    // monitoring page, which lives inside this shell, and to nothing else.
    server.use(
      http.get("/api/me", () =>
        HttpResponse.json({
          ...CURRENT_USER,
          role: "user",
          permissions: [],
          agent_access: true,
        }),
      ),
    );
    renderWithProviders({ initialRoute: "/acme-analytics/admin/agents" });

    expect(
      await screen.findByRole("button", { name: "Agents" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Admin access required")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Users" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Storage backends" }),
    ).not.toBeInTheDocument();
  });

  it("still blocks a user with neither permissions nor an agent grant", async () => {
    server.use(
      http.get("/api/me", () =>
        HttpResponse.json({
          ...CURRENT_USER,
          role: "user",
          permissions: [],
          agent_access: false,
        }),
      ),
    );
    renderWithProviders({ initialRoute: "/acme-analytics/admin/agents" });

    expect(
      await screen.findByText("Admin access required"),
    ).toBeInTheDocument();
  });
});
