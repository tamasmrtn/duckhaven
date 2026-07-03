import { describe, it, expect } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@tests/mock/server";
import { renderWithProviders } from "@tests/utils";

const ROUTE = "/acme-analytics/admin/access";

const GRANTS_PATH = "/api/workspaces/:ws/catalogs/:catalog/grants";

describe("GrantsPage", () => {
  it("shows the open-mode notice when a catalog is not scoped", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    expect(await screen.findByText("Access grants")).toBeInTheDocument();
    expect(await screen.findByText(/this catalog is open/i)).toBeInTheDocument();
  });

  it("renders the grant tree and editor for a scoped catalog", async () => {
    server.use(
      http.get(GRANTS_PATH, () =>
        HttpResponse.json({
          access_mode: "scoped",
          grants: [
            {
              id: "grant-1",
              user_id: "user-2",
              user_name: "Grace Hopper",
              schema_name: "marketing",
              table_name: null,
              tier: "reader",
              created_at: new Date().toISOString(),
            },
          ],
          principals: [
            {
              user_id: "user-2",
              name: "Grace Hopper",
              email: "grace@duckhaven.dev",
              role: "reader",
              is_service_account: false,
            },
          ],
        }),
      ),
    );
    renderWithProviders({ initialRoute: ROUTE });

    // The grant row is rendered with principal, scope, and tier.
    expect(await screen.findByText("Grace Hopper")).toBeInTheDocument();
    expect(screen.getByText("marketing.*")).toBeInTheDocument();
    // "reader" appears both as the grant's tier badge and the form default.
    expect(screen.getAllByText("reader").length).toBeGreaterThan(0);
    // The add-grant editor is visible in scoped mode.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /add grant/i })).toBeInTheDocument(),
    );
  });
});
