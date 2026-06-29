import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@tests/utils";

const ROUTE = "/acme-analytics/saved-queries";

describe("ScheduleDialog", () => {
  it("shows the run history for an existing schedule", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Daily events");

    // "Daily events" (sq-1) has a seeded schedule (sch-1) with two runs.
    await user.click(screen.getByRole("button", { name: "Schedule Daily events" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Run history")).toBeInTheDocument();
    // One done run and one failed run, rendered as StatusPills (role="status",
    // aria-labelled by status).
    expect(within(dialog).getByRole("status", { name: "done" })).toBeInTheDocument();
    expect(within(dialog).getByRole("status", { name: "failed" })).toBeInTheDocument();
  });

  it("creates a schedule for a query that has none", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Funnel overview");

    // "Funnel overview" (sq-2) has no schedule yet.
    const card = screen.getByTestId("sq-card-sq-2");
    expect(within(card).queryByText(/Next run/)).not.toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Schedule Funnel overview" }),
    );
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Schedule" }));

    // After creating, this card surfaces a next-run badge.
    await waitFor(() => {
      expect(within(card).getByText(/Next run/)).toBeInTheDocument();
    });
  });

  it("disables Save for an invalid cron expression", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Funnel overview");

    await user.click(
      screen.getByRole("button", { name: "Schedule Funnel overview" }),
    );
    const dialog = await screen.findByRole("dialog");
    const input = within(dialog).getByLabelText("Cron expression");
    await user.clear(input);
    await user.type(input, "not a cron");

    expect(within(dialog).getByRole("button", { name: "Schedule" })).toBeDisabled();
  });

  it("toggles a schedule off, clearing its next run", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Daily events");
    // sch-1 is enabled, so the "Daily events" card shows a next-run badge.
    const card = screen.getByTestId("sq-card-sq-1");
    expect(within(card).getByText(/Next run/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Schedule Daily events" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Enabled")); // uncheck
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(within(card).queryByText(/Next run/)).not.toBeInTheDocument();
    });
  });
});
