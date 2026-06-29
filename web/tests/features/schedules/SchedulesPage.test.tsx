import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@tests/utils";

const ROUTE = "/acme-analytics/schedules";

describe("SchedulesPage", () => {
  it("lists existing schedules on the Schedules tab", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    // sch-1 targets saved query sq-1 ("Daily events").
    expect(await screen.findByText("Daily events")).toBeInTheDocument();
    expect(screen.getByText("0 2 * * *")).toBeInTheDocument();
    expect(screen.getByText("enabled")).toBeInTheDocument();
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
    expect(within(dialog).getByRole("button", { name: "Remove" })).toBeInTheDocument();
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

    expect(within(dialog).getByRole("button", { name: "Create" })).toBeDisabled();
  });
});
