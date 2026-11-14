import { describe, expect, it } from "vitest";
import { http } from "msw";
import userEvent from "@testing-library/user-event";
import { server } from "@tests/mock/server";
import { renderWithProviders, screen, within } from "@tests/utils";
import { page } from "@/mock/lib/page";

const ROUTE = "/acme-analytics/dashboards";

describe("DashboardsPage", () => {
  it("lists the workspace's dashboards with where each stands", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    const sales = (await screen.findByText("Sales overview")).closest("tr")!;
    expect(within(sales).getByText("published")).toBeVisible();
    expect(within(sales).getByText("unpublished changes")).toBeVisible();
    expect(within(sales).getByText("UI")).toBeVisible();

    const finance = screen.getByText("Finance weekly").closest("tr")!;
    expect(within(finance).getByText("git")).toBeVisible();

    const churn = screen.getByText("Churn review").closest("tr")!;
    expect(within(churn).getByText("draft")).toBeVisible();
  });

  it("says when there are none", async () => {
    server.use(http.get("/api/workspaces/:ws/dashboards", () => page([])));
    renderWithProviders({ initialRoute: ROUTE });

    expect(await screen.findByText("No dashboards")).toBeVisible();
  });

  it("opens a dashboard from its row", async () => {
    const { router } = renderWithProviders({ initialRoute: ROUTE });

    await userEvent.click(await screen.findByText("Sales overview"));

    expect(router.state.location.pathname).toBe(
      "/acme-analytics/dashboards/sales-overview",
    );
  });
});
