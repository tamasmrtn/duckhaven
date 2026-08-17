import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { server } from "@tests/mock/server";
import { renderWithProviders, screen, within } from "@tests/utils";

const TABLE_ROUTE = "/acme-analytics/catalog/acme_analytics/raw/events";
const SEMANTIC_URL =
  "/api/workspaces/:ws/catalogs/:catalog/schemas/:schema/tables/:table/semantic";

async function openDropDialog() {
  await userEvent.click(await screen.findByRole("button", { name: /^drop$/i }));
  return screen.findByRole("dialog");
}

describe("BrokenByDropWarning", () => {
  it("names the published definitions a drop will break", async () => {
    // The consequence the catalog cannot work out for itself.
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    const dialog = await openDropDialog();

    expect(
      await within(dialog).findByText(/will break \d+ published definitions?/i),
    ).toBeVisible();
    expect(await within(dialog).findByText("revenue")).toBeVisible();
  });

  it("says the definitions survive the drop", async () => {
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    const dialog = await openDropDialog();

    expect(
      await within(dialog).findByText(/they are not\s+deleted/i),
    ).toBeVisible();
  });

  it("stays quiet when nothing depends on the table", async () => {
    server.use(
      http.get(SEMANTIC_URL, () => HttpResponse.json({ dependents: [] })),
    );
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    const dialog = await openDropDialog();

    expect(within(dialog).queryByText(/will break/i)).toBeNull();
  });

  it("ignores definitions that are still drafts", async () => {
    // A draft is not being quoted by anyone yet, so it is not a consequence
    // worth interrupting a drop for.
    server.use(
      http.get(SEMANTIC_URL, () =>
        HttpResponse.json({
          dependents: [
            {
              kind: "metric",
              model: "marketing",
              model_name: "Marketing",
              model_status: "draft",
              name: "spend",
              label: "Spend",
              status: "draft",
              dataset: "funnel",
              columns: ["cost_usd"],
            },
          ],
        }),
      ),
    );
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    const dialog = await openDropDialog();

    expect(within(dialog).queryByText(/will break/i)).toBeNull();
  });
});
