import { describe, it, expect } from "vitest";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@tests/utils";
import { renderWithProviders } from "@tests/utils";
import { server } from "@tests/mock/server";

// q-1 is a done SELECT in the history fixture, so the profile handler serves
// SAMPLE_PROFILE for it.
const ROUTE = "/acme-analytics/queries/q-1";

describe("QueryProfilePage", () => {
  it("renders the stats header, operator graph, and side panels", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    // Stats header.
    expect(await screen.findByText("Latency")).toBeInTheDocument();
    expect(screen.getByText("Peak memory")).toBeInTheDocument();

    // Graph nodes (one per operator type in SAMPLE_PROFILE).
    expect(screen.getByRole("button", { name: /ORDER_BY/, pressed: true })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /HASH_GROUP_BY/, pressed: false })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /SEQ_SCAN/, pressed: false })).toBeInTheDocument();

    // Side panels.
    expect(screen.getByText("Most expensive operators")).toBeInTheDocument();
    expect(screen.getByText("Diagnostics")).toBeInTheDocument();
  });

  it("selects a node on click and shows its detail", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    const scan = await screen.findByRole("button", { name: /SEQ_SCAN/, pressed: false });
    await user.click(scan);

    // The scan's estimated cardinality (2,000) is unique to its detail panel.
    expect(await screen.findByText("2,000")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /SEQ_SCAN/, pressed: true })).toBeInTheDocument();
  });

  it("flags spill and scan blow-up in diagnostics", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    expect(await screen.findByText(/Spilled to disk/i)).toBeInTheDocument();
    expect(screen.getByText(/Scan blow-up/i)).toBeInTheDocument();
  });

  it("shows a no-profile state when the profile is null", async () => {
    server.use(http.get("/api/queries/:id/profile", () => HttpResponse.json(null)));
    renderWithProviders({ initialRoute: ROUTE });
    await waitFor(() =>
      expect(screen.getByText(/No profile for this query/i)).toBeInTheDocument(),
    );
  });
});
