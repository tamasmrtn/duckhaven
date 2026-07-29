import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http } from "msw";
import { server } from "@tests/mock/server";
import { renderWithProviders } from "@tests/utils";
import { httpError } from "@/mock/lib/errors";

const ROUTE = "/acme-analytics/schedules";

describe("SchedulesPage", () => {
  it("lists existing schedules on the Schedules tab", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    // sch-1 targets saved query sq-1 ("Daily events").
    expect(await screen.findByText("Daily events")).toBeInTheDocument();
    expect(screen.getByText("0 2 * * *")).toBeInTheDocument();
    expect(screen.getByText("enabled")).toBeInTheDocument();
  });

  it("renders the schedules as a semantic table", async () => {
    // Regression for the migration onto the shared Table primitive.
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Daily events");
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "Cron" }),
    ).toBeInTheDocument();
  });

  it("shows all scheduled runs on the Runs tab", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Daily events");

    await user.click(screen.getByRole("tab", { name: "Runs" }));

    // The seeded feed has one done run and one failed run.
    expect(
      await screen.findByRole("status", { name: "done" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "failed" })).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("creates a new schedule from the New schedule dialog", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Daily events");

    await user.click(screen.getByRole("button", { name: "New schedule" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("New schedule")).toBeInTheDocument();
    // A saved-query selector is present (create flow only).
    expect(within(dialog).getByLabelText("Saved query")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Create" }));

    // Dialog closes after a successful create.
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  it("opens an existing schedule for editing with its run history", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await user.click(await screen.findByText("Daily events"));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Edit schedule")).toBeInTheDocument();
    expect(within(dialog).getByText("Recent runs")).toBeInTheDocument();
    // The edit dialog offers a Remove action.
    expect(
      within(dialog).getByRole("button", { name: "Remove" }),
    ).toBeInTheDocument();
  });

  it("disables Create for an invalid cron expression", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Daily events");

    await user.click(screen.getByRole("button", { name: "New schedule" }));
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText("Cron expression");
    await user.clear(input);
    await user.type(input, "nope");

    expect(
      within(dialog).getByRole("button", { name: "Create" }),
    ).toBeDisabled();
  });
});

describe("SchedulesPage agent access", () => {
  it("explains a revoked agent instead of closing the dialog", async () => {
    // The picker only offers agents you may use, but a grant can be revoked
    // while the dialog is open — the API is the authority, so surface its 403.
    server.use(
      http.post("/api/workspaces/:ws/schedules", () =>
        httpError(403, "agent_forbidden"),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Daily events");

    await user.click(screen.getByRole("button", { name: "New schedule" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Create" }));

    expect(
      await screen.findByText(/no longer have access to the selected agent/i),
    ).toBeInTheDocument();
    // The dialog stays open so the choice can be corrected.
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
