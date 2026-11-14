import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { server } from "@tests/mock/server";
import {
  configure,
  renderWithProviders,
  screen,
  waitFor,
  within,
} from "@tests/utils";
import { validationError } from "@/mock/lib/errors";

const ROUTE = "/acme-analytics/dashboards";
const RUN_URL = "/api/workspaces/:ws/dashboards/:name/run";

// These tests render the whole app and wait for a run to stream in. The first
// render in the file, charts included, takes about 0.7s on a fast machine and
// over 1s under coverage, past find*'s default 1000ms window. Same headroom as
// CatalogPage.test.tsx.
configure({ asyncUtilTimeout: 3000 });

async function widget(title: string) {
  return (await screen.findByRole("region", { name: title })) as HTMLElement;
}

async function answered() {
  await screen.findByText(/answered in/);
}

describe("DashboardPage", () => {
  it("shows each number with how it moved, coloured by whether that is good", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview` });
    await answered();

    const revenue = await widget("Revenue");
    expect(within(revenue).getByTestId("kpi-value")).toHaveTextContent(
      "$48.2K",
    );
    const up = within(revenue).getByTestId("kpi-delta");
    expect(up).toHaveTextContent("+12.5%");
    expect(up).toHaveTextContent("Up, good");
    expect(
      within(revenue).getByText(/compared with previous 30 days/i),
    ).toBeVisible();

    // A refund rate that rose is bad news, and moved by points, not percent.
    const refunds = await widget("Refund rate");
    const worse = within(refunds).getByTestId("kpi-delta");
    expect(worse).toHaveTextContent("Up, bad");
    expect(worse).toHaveTextContent("+0.4 pts");
  });

  it("draws only the rows of a long table that are in view", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview` });
    await answered();

    const orders = await widget("Latest orders");
    const table = within(orders).getByRole("table");
    expect(table).toHaveAttribute("aria-rowcount", "241");
    expect(within(table).getAllByRole("row").length).toBeLessThan(40);
    expect(within(orders).getByText("240 rows")).toBeVisible();
  });

  it("renders a text widget's markdown", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview` });

    expect(
      await screen.findByRole("link", { name: "the metric" }),
    ).toBeVisible();
  });

  it("says what each widget could not show, and why", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/finance-weekly` });
    await answered();

    expect(
      within(await widget("Gross margin")).getByText(
        "You don't have access to the data this widget reads.",
      ),
    ).toBeVisible();
    expect(
      within(await widget("Payouts")).getByText(/does not exist/),
    ).toBeVisible();
    expect(
      within(await widget("Refunds by reason")).getByText(
        /No rows for this window/,
      ),
    ).toBeVisible();
  });

  it("says that a dashboard is managed in git", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/finance-weekly` });

    expect(await screen.findByText(/Managed in git/)).toHaveTextContent(
      "acme/dashboards",
    );
  });

  it("says when widgets wait for compute to start, until they are answered", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/finance-weekly` });

    expect(await screen.findByText(/Compute is starting/)).toHaveTextContent(
      "1 widget waits for it.",
    );
    await answered();
    expect(screen.queryByText(/Compute is starting/)).not.toBeInTheDocument();
  });

  it("offers to edit a dashboard made in the app, and not one managed in git", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview` });

    expect(await screen.findByRole("link", { name: "Edit" })).toHaveAttribute(
      "href",
      `${ROUTE}/sales-overview/edit`,
    );
    // An owner hears about the draft waiting to be published.
    expect(
      await screen.findByText(/Unpublished changes wait in a draft/),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Review and publish them" }),
    ).toHaveAttribute("href", `${ROUTE}/sales-overview/edit`);
  });

  it("offers no editing of a dashboard managed in git", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/finance-weekly` });

    await screen.findByText(/Managed in git/);
    expect(screen.queryByRole("link", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("marks a dashboard nobody has published as a draft", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/churn-review` });

    expect(await screen.findByText(/only writers can see it/)).toBeVisible();
  });

  it("keeps the last answers on screen while a refresh runs", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview` });
    await answered();

    await userEvent.click(screen.getByRole("button", { name: /refresh/i }));

    const revenue = await widget("Revenue");
    expect(within(revenue).getByText("Updating…")).toBeVisible();
    expect(within(revenue).getByTestId("kpi-value")).toHaveTextContent(
      "$48.2K",
    );
    await waitFor(() =>
      expect(within(revenue).queryByText("Updating…")).not.toBeInTheDocument(),
    );
  });

  it("shows why a run was refused", async () => {
    server.use(
      http.post(RUN_URL, () =>
        validationError(
          "invalid_parameters",
          "'someday' is not a time range.",
          422,
          {
            parameter: "period",
          },
        ),
      ),
    );
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview` });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "'someday' is not a time range.",
    );
  });

  it("says when the stream ends before every widget is answered", async () => {
    server.use(
      http.post(
        RUN_URL,
        () =>
          new HttpResponse(
            'data: {"type":"start","run_id":"r","dashboard":{"name":"sales-overview","version":3,"spec_hash":"h","draft":false,"preview":null},"today":"2026-11-13","timezone":"UTC","parameters":[],"widgets":["revenue"]}\n\n',
            {
              headers: { "Content-Type": "text/event-stream" },
            },
          ),
      ),
    );
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview` });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      /connection closed/,
    );
    expect(
      within(await widget("Revenue")).getByText(
        "This widget was not answered.",
      ),
    ).toBeVisible();
  });

  it("says when there is no such dashboard", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/nope` });

    expect(await screen.findByText("No dashboard called nope")).toBeVisible();
  });
});
