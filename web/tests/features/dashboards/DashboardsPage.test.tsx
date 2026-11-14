import { describe, expect, it, vi } from "vitest";
import { http } from "msw";
import userEvent from "@testing-library/user-event";
import { server } from "@tests/mock/server";
import { renderWithProviders, screen, waitFor, within } from "@tests/utils";
import { page } from "@/mock/lib/page";

const ROUTE = "/acme-analytics/dashboards";

// The editor a new dashboard opens in: a textarea stands in for Monaco.
vi.mock("@monaco-editor/react", () => ({
  Editor: ({ value }: { value: string }) => (
    <textarea aria-label="Dashboard file" value={value} readOnly />
  ),
  default: () => null,
}));
vi.mock("@/features/dashboards/editor/yamlLoader", () => ({
  useDashboardYamlReady: () => true,
}));

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

  it("starts a dashboard as a draft and opens it in the editor", async () => {
    const { router } = renderWithProviders({ initialRoute: ROUTE });

    await userEvent.click(
      await screen.findByRole("button", { name: "New dashboard" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "New dashboard" });
    await userEvent.type(within(dialog).getByLabelText("Title"), "Café revenue");
    // The name follows the title until it is typed over.
    expect(within(dialog).getByLabelText("Name")).toHaveValue("cafe-revenue");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(router.state.location.pathname).toBe(
        "/acme-analytics/dashboards/cafe-revenue/edit",
      ),
    );
    const file = await screen.findByRole("textbox", { name: "Dashboard file" });
    expect((file as HTMLTextAreaElement).value).toContain('title: "Café revenue"');
  });

  it("says when the name is taken, and offers no name the server would refuse", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    await userEvent.click(
      await screen.findByRole("button", { name: "New dashboard" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "New dashboard" });
    const create = within(dialog).getByRole("button", { name: "Create" });
    await userEvent.type(within(dialog).getByLabelText("Title"), "Sales overview");
    await userEvent.click(create);

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "already has a dashboard called 'sales-overview'",
    );

    const name = within(dialog).getByLabelText("Name");
    await userEvent.clear(name);
    await userEvent.type(name, "Sales 2");
    expect(create).toBeDisabled();
  });
});
