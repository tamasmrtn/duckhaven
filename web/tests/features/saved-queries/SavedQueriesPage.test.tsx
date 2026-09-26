import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "@tests/utils";
import { server } from "@tests/mock/server";
import { SAVED_QUERIES } from "@/mock/fixtures/queries";
import { WORKSHEETS } from "@/mock/fixtures/worksheets";

const ROUTE = "/acme-analytics/saved-queries";

describe("SavedQueriesPage", () => {
  it("lists saved queries for the workspace", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    expect(await screen.findByText("Daily events")).toBeInTheDocument();
  });

  it("shows who saved each query", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Daily events");
    expect(screen.getAllByText(/Saved by Marton/).length).toBeGreaterThan(0);
  });

  it("shows an empty state when the workspace has no saved queries", async () => {
    server.use(
      http.get("/api/workspaces/:ws/saved-queries", () =>
        HttpResponse.json({ items: [], cursor: null, has_more: false }),
      ),
    );
    renderWithProviders({ initialRoute: ROUTE });
    expect(
      await screen.findByText("Save a worksheet to keep it here"),
    ).toBeInTheDocument();
  });

  it('"Open" opens a worksheet named for the query, on its saved agent', async () => {
    // A saved query whose default agent (ag-2 / agent-b) differs from the
    // worksheet default (ag-1 / agent-a), so a pre-selection is observable.
    SAVED_QUERIES.push({
      id: "sq-x",
      name: "Agent-bound query",
      sql: "SELECT 99",
      workspace_id: "ws-1",
      default_agent_id: "ag-2",
      created_by: "u-1",
      created_at: "2026-05-01T00:00:00Z",
      updated_at: "2026-05-01T00:00:00Z",
      updated_by: "u-1",
      last_run_at: null,
    });
    const user = userEvent.setup();
    const { router } = renderWithProviders({ initialRoute: ROUTE });
    const card = (await screen.findByText("Agent-bound query")).closest(
      "div.flex-col",
    ) as HTMLElement;

    await user.click(within(card).getByRole("button", { name: "Open" }));

    await waitFor(() => {
      expect(router.state.location.pathname).toBe("/acme-analytics/worksheets");
    });
    const tab = await screen.findByRole("tab", { name: /Agent-bound query/ });
    await waitFor(() =>
      expect(
        screen.getByRole("combobox", { name: "Compute agent" }),
      ).toHaveTextContent("agent-b"),
    );
    // Linked, and nothing to save yet.
    expect(WORKSHEETS.at(-1)).toMatchObject({ saved_query_id: "sq-x" });
    expect(
      within(tab).queryByLabelText("unsaved changes to saved query"),
    ).not.toBeInTheDocument();
  });

  it('"Open" focuses the worksheet already linked to a query', async () => {
    // wk-2 ("funnel-draft") is linked to sq-2 ("Funnel overview").
    const user = userEvent.setup();
    const before = WORKSHEETS.length;
    renderWithProviders({ initialRoute: ROUTE });
    const card = (await screen.findByText("Funnel overview")).closest(
      "div.flex-col",
    ) as HTMLElement;

    await user.click(within(card).getByRole("button", { name: "Open" }));

    const tab = await screen.findByRole("tab", { name: /funnel-draft/ });
    await waitFor(() => expect(tab).toHaveAttribute("data-state", "active"));
    expect(WORKSHEETS).toHaveLength(before);
  });

  it("searches names and SQL", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Daily events");

    await user.type(screen.getByLabelText("Search saved queries"), "funnel");

    expect(screen.getByText("Funnel overview")).toBeInTheDocument();
    expect(screen.queryByText("Daily events")).not.toBeInTheDocument();
  });

  it("sorts by name", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Daily events");

    await user.click(screen.getByRole("button", { name: "Name" }));

    const names = screen
      .getAllByText(/^(Daily events|Funnel overview)$/)
      .map((el) => el.textContent);
    expect(names).toEqual(["Daily events", "Funnel overview"]);
  });

  it("says who last changed a query when it was not its creator", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Funnel overview");
    // sq-2 was saved by Marton and last edited by Jess.
    expect(screen.getByText(/Updated .* by Jess/)).toBeInTheDocument();
  });

  it("refuses to rename onto a name already in use", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Daily events");

    await user.click(screen.getByRole("button", { name: "Rename Daily events" }));
    const input = screen.getByLabelText("Name");
    await user.clear(input);
    await user.type(input, "funnel OVERVIEW");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists/);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("deletes a saved query", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Daily events");

    await user.click(
      screen.getByRole("button", { name: "Delete Daily events" }),
    );
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => {
      expect(screen.queryByText("Daily events")).not.toBeInTheDocument();
    });
    expect(screen.getByText("Funnel overview")).toBeInTheDocument();
  });

  it("renames a saved query", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Daily events");

    await user.click(
      screen.getByRole("button", { name: "Rename Daily events" }),
    );
    const input = screen.getByLabelText("Name");
    await user.clear(input);
    await user.type(input, "Weekly events");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Weekly events")).toBeInTheDocument();
    expect(screen.queryByText("Daily events")).not.toBeInTheDocument();
  });
});
