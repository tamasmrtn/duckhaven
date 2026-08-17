import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { server } from "@tests/mock/server";
import { renderWithProviders, screen } from "@tests/utils";

const TABLE_ROUTE = "/acme-analytics/catalog/acme_analytics/raw/events";
const SEMANTIC_URL =
  "/api/workspaces/:ws/catalogs/:catalog/schemas/:schema/tables/:table/semantic";

async function openSemanticsTab() {
  await userEvent.click(await screen.findByRole("tab", { name: /semantics/i }));
}

describe("SemanticPanel", () => {
  it("appears as a tab on the table detail view", async () => {
    renderWithProviders({ initialRoute: TABLE_ROUTE });

    expect(await screen.findByRole("tab", { name: /semantics/i })).toBeVisible();
  });

  it("lists the definitions that depend on this table", async () => {
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openSemanticsTab();

    expect(await screen.findByText("Revenue")).toBeVisible();
  });

  it("names the columns each definition reads", async () => {
    // The question asked immediately before somebody drops a column.
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openSemanticsTab();

    expect(await screen.findByText(/reads .*amount/)).toBeVisible();
  });

  it("says plainly when nothing depends on the table", async () => {
    server.use(
      http.get(SEMANTIC_URL, () => HttpResponse.json({ dependents: [] })),
    );
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openSemanticsTab();

    expect(
      await screen.findByText(/No semantic definitions use this table/i),
    ).toBeVisible();
  });

  it("links through to the model that owns a definition", async () => {
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openSemanticsTab();

    await userEvent.click(await screen.findByText("Revenue"));

    expect(await screen.findByRole("tab", { name: /joins/i })).toBeVisible();
  });
});
