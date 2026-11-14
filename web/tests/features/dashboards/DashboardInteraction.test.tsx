import { cloneElement, type ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { http } from "msw";
import userEvent from "@testing-library/user-event";
import { server } from "@tests/mock/server";
import {
  configure,
  fireEvent,
  renderWithProviders,
  screen,
  waitFor,
  within,
} from "@tests/utils";
import type { DashboardRunRequest } from "@/types/dashboard";

// jsdom lays nothing out, so a responsive chart never learns its size and
// draws no bars. Give it one, so there is a bar to click.
vi.mock("recharts", async (importOriginal) => {
  const recharts = await importOriginal<typeof import("recharts")>();
  return {
    ...recharts,
    ResponsiveContainer: ({ children }: { children: ReactElement }) =>
      cloneElement(children as ReactElement<Record<string, unknown>>, {
        width: 400,
        height: 224,
      }),
  };
});

const ROUTE = "/acme-analytics/dashboards/sales-overview";
const RUN_URL = "/api/workspaces/:ws/dashboards/:name/run";

// These tests render the whole app and wait for a run to stream in, which
// can take over 1s under coverage. Same headroom as DashboardPage.test.tsx.
configure({ asyncUtilTimeout: 3000 });

function recordRuns(): DashboardRunRequest[] {
  const bodies: DashboardRunRequest[] = [];
  server.use(
    http.post(RUN_URL, async ({ request }) => {
      bodies.push((await request.clone().json()) as DashboardRunRequest);
    }),
  );
  return bodies;
}

async function byCountry() {
  await screen.findByText(/answered in/);
  return (await screen.findByRole("region", {
    name: "Revenue by country",
  })) as HTMLElement;
}

/** The clickable band of each bar, in the order the rows came. */
function bands(widget: HTMLElement) {
  return [
    ...widget.querySelectorAll(".recharts-bar-background-rectangle"),
  ] as Element[];
}

describe("cross-filtering", () => {
  it("filters the dashboard by a clicked bar, and a second click clears it", async () => {
    const runs = recordRuns();
    renderWithProviders({ initialRoute: ROUTE });
    const widget = await byCountry();

    // NL is the first bar.
    fireEvent.click(bands(widget)[0]);
    await waitFor(() => expect(runs).toHaveLength(2));
    expect(runs[1].params).toEqual({ country: "NL" });

    // The chart keeps every country and marks the chosen one.
    await waitFor(() =>
      expect(
        within(widget).getByRole("img", { name: /NL is selected\.$/ }),
      ).toBeInTheDocument(),
    );
    expect(bands(widget)).toHaveLength(5);
    // The filter bar shows the same choice.
    expect(screen.getByRole("textbox", { name: "Country" })).toHaveValue("NL");

    fireEvent.click(bands(await byCountry())[0]);
    await waitFor(() => expect(runs).toHaveLength(3));
    expect(runs[2].params).toEqual({});
  });

  it("sets nothing from a line chart", async () => {
    const runs = recordRuns();
    renderWithProviders({ initialRoute: ROUTE });
    await byCountry();
    const trend = screen.getByRole("region", { name: "Revenue by month" });

    expect(bands(trend)).toHaveLength(0);
    expect(runs).toHaveLength(1);
  });
});

describe("present mode", () => {
  it("shows the dashboard alone, refreshes on request, and runs nothing to enter", async () => {
    const runs = recordRuns();
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await byCountry();

    await user.click(screen.getByRole("button", { name: "Present" }));
    const presented = await screen.findByRole("dialog", {
      name: "Sales overview, presented",
    });
    expect(
      within(presented).getByRole("region", { name: "Revenue by country" }),
    ).toBeInTheDocument();
    expect(runs).toHaveLength(1);

    await user.click(
      within(presented).getByRole("combobox", { name: "Refresh every" }),
    );
    expect(
      await screen.findByText(/refreshing keeps compute awake/),
    ).toBeVisible();
    await user.click(screen.getByRole("option", { name: "Every 15 minutes" }));
    expect(
      within(presented).getByRole("combobox", { name: "Refresh every" }),
    ).toHaveTextContent("Every 15 minutes");

    await user.click(
      within(presented).getByRole("button", { name: "Exit present mode" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Present" })).toBeVisible();
    expect(runs).toHaveLength(1);
  });

  it("opens straight into present mode from its link", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}?present=true&refresh=30` });

    const presented = await screen.findByRole("dialog", {
      name: "Sales overview, presented",
    });
    expect(
      within(presented).getByRole("combobox", { name: "Refresh every" }),
    ).toHaveTextContent("Every 30 minutes");
  });
});
