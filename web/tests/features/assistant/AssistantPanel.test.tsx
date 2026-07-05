import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@tests/utils";

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
