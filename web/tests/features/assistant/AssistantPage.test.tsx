import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@tests/utils";

const ROUTE = "/acme-analytics/assistant";

describe("AssistantPage", () => {
  it("lists conversations and shows the selected transcript", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    expect(await screen.findByText("Exploring events")).toBeInTheDocument();
    // The seeded conversation's transcript renders.
    expect(
      await screen.findByText("There are 42 events in the events table."),
    ).toBeInTheDocument();
    // Its tool-call audit trail is shown.
    expect(screen.getByText("Activity")).toBeInTheDocument();
    expect(screen.getByText("run_sql")).toBeInTheDocument();
  });

  it("streams an answer for a new message", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Exploring events");

    const box = screen.getByLabelText("Message");
    await user.type(box, "what tables exist?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(
      await screen.findByText("Here is what I found."),
    ).toBeInTheDocument();
  });

  it("prompts for approval when the assistant proposes a write", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Exploring events");

    const box = screen.getByLabelText("Message");
    await user.type(box, "delete all the events");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Approve write?")).toBeInTheDocument();
    expect(within(dialog).getByText("DELETE FROM events")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: /Approve/ }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(
      await screen.findByText("Done — the write ran."),
    ).toBeInTheDocument();
  });

  it("creates a new conversation", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Exploring events");

    await user.click(screen.getByRole("button", { name: "New conversation" }));
    // The new empty conversation becomes selected; the composer is ready.
    await waitFor(() =>
      expect(screen.getByLabelText("Message")).toBeInTheDocument(),
    );
  });
});
