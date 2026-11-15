import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { server } from "@tests/mock/server";
import { configure, renderWithProviders, screen, within } from "@tests/utils";
import { dashboardsApi } from "@/api/dashboards";
import { PREVIEW_FOLLOW_MS } from "@/queries/dashboards";

// A change to the file shows once the page next reads the preview, a couple of
// seconds on, and the run it starts streams in after that.
configure({ asyncUtilTimeout: PREVIEW_FOLLOW_MS + 3000 });
const SLOW = { timeout: 15_000 };

const WS = "acme-analytics";

/** A file whose widgets the mock answers as the sales dashboard's do. */
function file(title: string, widget = "revenue") {
  return [
    "version: 1",
    "name: sales-weekly",
    `title: ${title}`,
    "rows:",
    "  - widgets:",
    `      - {id: ${widget}, type: kpi, title: ${widget}, query: {model: sales}}`,
    "",
  ].join("\n");
}

async function answered() {
  const preview = await screen.findByRole("region", { name: "Preview" });
  await within(preview).findByText(/answered in/);
  return preview;
}

describe("DashboardPreviewPage", SLOW, () => {
  it("runs the file the preview holds, as the person viewing it", async () => {
    const held = await dashboardsApi.createPreview(WS, file("Sales, weekly"));
    renderWithProviders({
      initialRoute: `/${WS}/dashboard-previews/${held.id}`,
    });

    const preview = await answered();

    expect(
      screen.getByRole("heading", { name: "Sales, weekly" }),
    ).toBeVisible();
    expect(
      screen.getByText(/A preview of sales-weekly.dashboard.yml/),
    ).toBeVisible();
    expect(within(preview).getByText("Preview, viewing as you")).toBeVisible();
    expect(
      within(preview).getByRole("region", { name: "revenue" }),
    ).toBeVisible();
    // Nothing on the page edits the file: it is changed where it is saved.
    expect(screen.queryByRole("button", { name: /Arrange/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Move row/ })).toBeNull();
  });

  it("follows the file as each save replaces it", async () => {
    const held = await dashboardsApi.createPreview(WS, file("Sales, weekly"));
    renderWithProviders({
      initialRoute: `/${WS}/dashboard-previews/${held.id}`,
    });
    await answered();

    await dashboardsApi.replacePreview(
      WS,
      held.id,
      file("Sales, by week", "orders"),
    );

    expect(
      await screen.findByRole("heading", { name: "Sales, by week" }),
    ).toBeVisible();
    const preview = await answered();
    expect(
      await within(preview).findByRole("region", { name: "orders" }),
    ).toBeVisible();
    expect(
      within(preview).queryByRole("region", { name: "revenue" }),
    ).toBeNull();
  });

  it("says how to see a preview that has ended", async () => {
    renderWithProviders({ initialRoute: `/${WS}/dashboard-previews/gone` });

    expect(await screen.findByText("This preview has ended")).toBeVisible();
    expect(screen.getByText(/Preview the file again/)).toBeVisible();
  });

  it("keeps showing the last file once the preview ends", async () => {
    const held = await dashboardsApi.createPreview(WS, file("Sales, weekly"));
    renderWithProviders({
      initialRoute: `/${WS}/dashboard-previews/${held.id}`,
    });
    await answered();

    server.use(
      http.get(
        "/api/workspaces/:ws/dashboard-previews/:id",
        () => new HttpResponse(null, { status: 404 }),
      ),
    );

    expect(
      await screen.findByText(/This preview has ended, so the page shows/),
    ).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Sales, weekly" }),
    ).toBeVisible();
  });
});
