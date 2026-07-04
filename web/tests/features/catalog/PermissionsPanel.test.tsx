import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@tests/mock/server";
import { createWrapper } from "@tests/utils";
import { PermissionsPanel } from "@/features/catalog/PermissionsPanel";

const PATH = "/api/workspaces/:ws/catalogs/:catalog/grants";

function scoped(grants: unknown[]) {
  return {
    access_mode: "scoped",
    grants,
    principals: [
      {
        user_id: "user-2",
        name: "Grace Hopper",
        email: "grace@duckhaven.dev",
        role: "reader",
        is_service_account: false,
      },
    ],
  };
}

const CATALOG_GRANT = {
  id: "g1",
  user_id: "user-2",
  user_name: "Grace Hopper",
  schema_name: null,
  table_name: null,
  tier: "reader",
  created_at: new Date().toISOString(),
};

describe("PermissionsPanel", () => {
  it("shows the access-mode toggle and direct grants at catalog scope", async () => {
    server.use(
      http.get(PATH, () => HttpResponse.json(scoped([CATALOG_GRANT]))),
    );
    const { wrapper } = createWrapper();
    render(<PermissionsPanel ws="ws" catalog="cat" />, { wrapper });

    expect(await screen.findByText("Access mode")).toBeInTheDocument();
    expect(screen.getByText("Grace Hopper")).toBeInTheDocument();
    // "reader" appears as the grant's tier badge and the add-form default.
    expect(screen.getAllByText("reader").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Grant" })).toBeInTheDocument();
  });

  it("shows a catalog grant as inherited (read-only) when viewing a table", async () => {
    server.use(
      http.get(PATH, () => HttpResponse.json(scoped([CATALOG_GRANT]))),
    );
    const { wrapper } = createWrapper();
    render(
      <PermissionsPanel
        ws="ws"
        catalog="cat"
        schema="marketing"
        table="leads"
      />,
      { wrapper },
    );

    expect(await screen.findByText("Inherited")).toBeInTheDocument();
    expect(screen.getByText(/inherited from catalog/i)).toBeInTheDocument();
    // Access mode is a catalog-only setting — not shown at table scope.
    expect(screen.queryByText("Access mode")).not.toBeInTheDocument();
  });
});
