import { describe, it, expect } from "vitest";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { screen, fireEvent, waitFor, within } from "@tests/utils";
import { renderWithProviders } from "@tests/utils";
import { server } from "@tests/mock/server";

// q-1 is a done SELECT in the history fixture, so the profile handler serves
// SAMPLE_PROFILE for it.
const ROUTE = "/acme-analytics/queries/q-1";

describe("QueryProfilePage", () => {
  it("shows the query SQL panel and expands it in place, not in a dialog", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    const sqlSnippet = /SELECT date_trunc\('day', event_time\)/;
    expect(await screen.findByText(sqlSnippet)).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /expand sql/i }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /collapse sql/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(sqlSnippet)).toBeInTheDocument();
  });

  it("renders the stats header, operator graph, and side panels", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    // Stats header.
    expect(await screen.findByText("Latency")).toBeInTheDocument();
    expect(screen.getByText("Peak memory")).toBeInTheDocument();

    // Graph nodes (one per operator type in SAMPLE_PROFILE).
    expect(
      screen.getByRole("button", { name: /ORDER_BY/, pressed: true }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /HASH_GROUP_BY/, pressed: false }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /ICEBERG_SCAN/, pressed: false }),
    ).toBeInTheDocument();

    // Side panels.
    expect(screen.getByText("Most expensive operators")).toBeInTheDocument();
    expect(screen.getByText("Diagnostics")).toBeInTheDocument();
  });

  it("zooms the operator graph in and out, and resets to 100%", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Latency");

    expect(
      screen.getByRole("button", { name: /reset zoom/i }),
    ).toHaveTextContent("100%");

    await user.click(screen.getByRole("button", { name: /zoom in/i }));
    expect(
      screen.getByRole("button", { name: /reset zoom/i }),
    ).toHaveTextContent("110%");

    await user.click(screen.getByRole("button", { name: /zoom out/i }));
    await user.click(screen.getByRole("button", { name: /zoom out/i }));
    expect(
      screen.getByRole("button", { name: /reset zoom/i }),
    ).toHaveTextContent("90%");

    await user.click(screen.getByRole("button", { name: /reset zoom/i }));
    expect(
      screen.getByRole("button", { name: /reset zoom/i }),
    ).toHaveTextContent("100%");
  });

  it("pans the graph by dragging with the left mouse button", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    const scroller = await screen.findByTestId("profile-graph-scroll");
    scroller.scrollLeft = 0;
    scroller.scrollTop = 0;

    fireEvent.mouseDown(scroller, { button: 0, clientX: 200, clientY: 200 });
    fireEvent.mouseMove(window, { clientX: 150, clientY: 170 });

    // Dragging left/up moves the viewport right/down, like grabbing a canvas.
    expect(scroller.scrollLeft).toBe(50);
    expect(scroller.scrollTop).toBe(30);

    fireEvent.mouseUp(window);
    fireEvent.mouseMove(window, { clientX: 0, clientY: 0 });

    // Movement after release no longer pans.
    expect(scroller.scrollLeft).toBe(50);
    expect(scroller.scrollTop).toBe(30);
  });

  it("does not start panning when the press begins on a node", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    const scroller = await screen.findByTestId("profile-graph-scroll");
    const node = screen.getByRole("button", {
      name: /ICEBERG_SCAN/,
      pressed: false,
    });
    scroller.scrollLeft = 10;

    fireEvent.mouseDown(node, { button: 0, clientX: 200, clientY: 200 });
    fireEvent.mouseMove(window, { clientX: 100, clientY: 100 });

    expect(scroller.scrollLeft).toBe(10);
  });

  it("selects a node on click and shows its detail", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    const scan = await screen.findByRole("button", {
      name: /ICEBERG_SCAN/,
      pressed: false,
    });
    await user.click(scan);

    // The scan's estimated cardinality (2,000) is unique to its detail panel.
    expect(await screen.findByText("2,000")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /ICEBERG_SCAN/, pressed: true }),
    ).toBeInTheDocument();
  });

  it("surfaces the reserved memory and CPU in the stats header", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    expect(await screen.findByText("Reserved mem")).toBeInTheDocument();
    expect(screen.getByText("Reserved CPU")).toBeInTheDocument();
    expect(screen.getByText("2 threads")).toBeInTheDocument();
  });

  it("flags spill and scan blow-up in diagnostics, citing the reservation", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    expect(await screen.findByText(/Spilled to disk/i)).toBeInTheDocument();
    expect(screen.getByText(/Scan blow-up/i)).toBeInTheDocument();
    // The spill diagnostic cites the reservation it spilled over.
    expect(
      screen.getByText(/spilled over a .* reservation/i),
    ).toBeInTheDocument();
  });

  it("identifies the expensive operators instead of repeating their type", async () => {
    // The ranked list used to render node.type, so a three-table join read
    // "TABLE_SCAN, TABLE_SCAN, TABLE_SCAN" and you had to click each entry to
    // find out which table was slow.
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Most expensive operators");

    // The ranked list and the diagnostics both identify the operator. The
    // graph node itself keeps the bare type — its box is 200px wide and the
    // type is the right label at that size.
    const ranked = screen
      .getByText("Most expensive operators")
      .closest("div")!.parentElement!;
    expect(
      within(ranked).getAllByText("Scan analytics.events (3 files)").length,
    ).toBeGreaterThan(0);
    expect(within(ranked).queryByText("ICEBERG_SCAN")).not.toBeInTheDocument();
  });

  it("shows where the time went, by kind of operator", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    // Labelled as a share of operator time, never of latency: operator timings
    // are self time and a parallel plan overlaps them.
    expect(
      await screen.findByText("Share of operator time"),
    ).toBeInTheDocument();
    expect(screen.getByText("Scans")).toBeInTheDocument();
    expect(screen.getByText("Sorts")).toBeInTheDocument();
  });

  it("reports scan effectiveness from the counters DuckDB actually emits", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Most expensive operators");

    await user.click(
      (await screen.findAllByText("Scan analytics.events (3 files)"))[0],
    );

    expect(await screen.findByText("Scan effectiveness")).toBeInTheDocument();
    // Iceberg reports files read and nothing about files skipped, so that is
    // all this claims. Never a byte-pruning figure: DuckDB reports none.
    expect(screen.getByText("Files read")).toBeInTheDocument();
    expect(screen.queryByText(/bytes pruned/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/pruned/i)).not.toBeInTheDocument();
  });

  it("surfaces admission wait and blocked time in the stats header", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    // Collected on every run and, until now, shown nowhere.
    expect(await screen.findByText("Admission wait")).toBeInTheDocument();
    expect(screen.getByText("Blocked")).toBeInTheDocument();
  });

  it("shows a no-profile state when the profile is null", async () => {
    server.use(
      http.get("/api/queries/:id/profile", () => HttpResponse.json(null)),
    );
    renderWithProviders({ initialRoute: ROUTE });
    await waitFor(() =>
      expect(
        screen.getByText(/No profile for this query/i),
      ).toBeInTheDocument(),
    );
  });
});
