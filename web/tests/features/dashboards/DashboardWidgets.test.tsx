import { afterEach, describe, expect, it, vi } from "vitest";
import { http } from "msw";
import userEvent from "@testing-library/user-event";
import { server } from "@tests/mock/server";
import {
  configure,
  renderWithProviders,
  screen,
  waitFor,
  within,
} from "@tests/utils";
import type { DashboardRunRequest } from "@/types/dashboard";

const ROUTE = "/acme-analytics/dashboards/sales-overview";
const RUN_URL = "/api/workspaces/:ws/dashboards/:name/run";

// These tests render the whole app and wait for a run to stream in. The first
// render in the file, charts included, takes about 0.7s on a fast machine and
// over 1s under coverage, past find*'s default 1000ms window. Same headroom as
// CatalogPage.test.tsx.
configure({ asyncUtilTimeout: 3000 });

/** Records each run's request, then lets the mock answer it. */
function recordRuns(): DashboardRunRequest[] {
  const bodies: DashboardRunRequest[] = [];
  server.use(
    http.post(RUN_URL, async ({ request }) => {
      bodies.push((await request.clone().json()) as DashboardRunRequest);
    }),
  );
  return bodies;
}

async function widget(title: string) {
  await screen.findByText(/answered in/);
  return (await screen.findByRole("region", { name: title })) as HTMLElement;
}

async function action(title: string, item: string) {
  const user = userEvent.setup();
  await user.click(
    within(await widget(title)).getByRole("button", {
      name: `Actions for ${title}`,
    }),
  );
  await user.click(await screen.findByRole("menuitem", { name: item }));
  return user;
}

describe("dashboard widgets", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("draws a line as a picture with a text alternative, and its rows on request", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    const trend = await widget("Revenue by month");

    expect(
      within(trend).getByRole("img", {
        name: "Revenue by month. A line chart of Revenue by month, one line per channel: Web, Retail.",
      }),
    ).toBeInTheDocument();
    // Two series: a legend names each, so no colour has to be matched by eye.
    expect(within(trend).getByText("Web")).toBeVisible();
    expect(within(trend).getByText("Retail")).toBeVisible();

    await action("Revenue by month", "View as table");
    expect(within(trend).queryByRole("img")).not.toBeInTheDocument();
    expect(within(trend).getByRole("table")).toBeVisible();
    expect(within(trend).getByText("12 rows")).toBeVisible();
  });

  it("says what a widget covers, and which filter it was not given and why", async () => {
    const runs = recordRuns();
    renderWithProviders({ initialRoute: `${ROUTE}?p.country=NL` });

    const byCountry = await widget("Revenue by country");
    expect(runs[0].params).toEqual({ country: "NL" });
    expect(within(byCountry).getByTestId("widget-scope")).toHaveTextContent(
      "Not filtered by Country: it is what this widget breaks down by.",
    );
    expect(
      within(await widget("Revenue by month")).getByTestId("widget-scope"),
    ).toHaveTextContent("Last 6 months: Jun – Nov 2026");
  });

  it("runs again for a window chosen in the filter bar", async () => {
    const runs = recordRuns();
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await widget("Revenue");

    await user.click(screen.getByRole("combobox", { name: "period" }));
    await user.click(
      await screen.findByRole("option", { name: "Last 7 days" }),
    );

    await waitFor(() => expect(runs).toHaveLength(2));
    expect(runs[1].params).toEqual({ period: "last_7_days" });

    await user.click(screen.getByRole("button", { name: /reset filters/i }));
    await waitFor(() => expect(runs).toHaveLength(3));
    expect(runs[2].params).toEqual({});
  });

  it("filters by a typed value when the model lists none to choose from", async () => {
    const runs = recordRuns();
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await widget("Revenue");

    await user.type(
      screen.getByRole("textbox", { name: "Country" }),
      "NL{Enter}",
    );

    await waitFor(() => expect(runs).toHaveLength(2));
    expect(runs[1].params).toEqual({ country: "NL" });
  });

  it("explains how a widget's metric is defined", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    await user.click(
      within(await widget("Revenue")).getByRole("button", {
        name: "How Revenue is defined",
      }),
    );

    const popover = await screen.findByRole("dialog");
    expect(
      within(popover).getByText(
        "SUM(total_amount) FILTER (WHERE status = 'paid')",
      ),
    ).toBeVisible();
    expect(
      within(popover).getByText("Excludes internal test orders."),
    ).toBeVisible();
    expect(within(popover).getByRole("link", { name: "sales" })).toBeVisible();
  });

  it("shows the SQL a widget ran", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    await action("Revenue", "View SQL");

    const dialog = await screen.findByRole("dialog", {
      name: "SQL for Revenue",
    });
    expect(dialog).toHaveTextContent("SELECT SUM(total_amount)");
  });

  it("downloads a widget's rows as CSV", async () => {
    const create = vi
      .spyOn(URL, "createObjectURL")
      .mockReturnValue("blob:rows");
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    renderWithProviders({ initialRoute: ROUTE });

    await action("Revenue by country", "Download CSV");

    expect(click).toHaveBeenCalledOnce();
    const csv = await (create.mock.calls[0][0] as Blob).text();
    expect(csv.split("\r\n").slice(0, 2)).toEqual([
      "country,revenue",
      "NL,18420.00",
    ]);
  });

  it("copies a link that lands on the widget", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    await action("Revenue", "Copy link");

    expect(await navigator.clipboard.readText()).toMatch(/#widget-revenue$/);
  });
});
