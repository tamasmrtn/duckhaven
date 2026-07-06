import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@tests/utils";
import { setAssistantEnabled } from "@/mock/fixtures/assistant";

// The panel is a right-side dock opened from the top bar; render a workspace page
// so the worksheet editor bridge is live for the propose-edit test.
const ROUTE = "/acme-analytics/worksheets";

async function openPanel(user: ReturnType<typeof userEvent.setup>) {
  const toggle = await screen.findByRole("button", {
    name: "Toggle AI assistant",
  });
  await user.click(toggle);
}

describe("AssistantPanel", () => {
  it("opens from the top bar and shows the seeded conversation", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);

    expect(
      await screen.findByRole("complementary", { name: "AI assistant" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("There are 42 events in the events table."),
    ).toBeInTheDocument();
  });

  it("collapses the Activity trace by default and expands it on click", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    // Collapsed: a count is shown but the tool rows are hidden.
    const toggle = screen.getByRole("button", { name: /Activity \(1\)/ });
    expect(screen.queryByText("run_sql")).not.toBeInTheDocument();

    await user.click(toggle);
    expect(screen.getByText("run_sql")).toBeInTheDocument();
  });

  it("echoes the user's message immediately, before the reply streams in", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    const probe = "unique probe message one two three";
    await user.type(screen.getByLabelText("Message"), probe);
    await user.click(screen.getByRole("button", { name: "Send" }));

    // The user's message is visible right away (optimistic echo) while the
    // assistant reply is still streaming and not yet shown.
    expect(screen.getByText(probe)).toBeInTheDocument();
    expect(screen.queryByText("Here is what I found.")).not.toBeInTheDocument();

    // The reply then arrives, and the user's message remains (now persisted).
    expect(
      await screen.findByText("Here is what I found."),
    ).toBeInTheDocument();
    expect(screen.getByText(probe)).toBeInTheDocument();
  });

  it("streams an answer for a new message", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.type(screen.getByLabelText("Message"), "how many rows total?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(
      await screen.findByText("Here is what I found."),
    ).toBeInTheDocument();
  });

  it("prompts for approval when the assistant proposes a write", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.type(screen.getByLabelText("Message"), "delete the events");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Approve write?")).toBeInTheDocument();
    expect(within(dialog).getByText("DELETE FROM events")).toBeInTheDocument();
  });

  it("shows a turned-off notice and disables input when the assistant is off", async () => {
    setAssistantEnabled(false);
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);

    expect(
      await screen.findByText("Assistant is turned off"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/ask a DuckHaven admin to turn it on/i),
    ).toBeInTheDocument();
    // The composer is present but disabled.
    expect(screen.getByLabelText("Message")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("proposes an editor edit that the user can accept", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.type(screen.getByLabelText("Message"), "write me a query");
    await user.click(screen.getByRole("button", { name: "Send" }));

    // The worksheet shows the AI-proposal bar with accept/reject.
    expect(
      await screen.findByText(/Assistant proposed changes/),
    ).toBeInTheDocument();
    const accept = screen.getByRole("button", { name: /Accept/ });
    await user.click(accept);
    await waitFor(() =>
      expect(
        screen.queryByText(/Assistant proposed changes/),
      ).not.toBeInTheDocument(),
    );
  });
});
