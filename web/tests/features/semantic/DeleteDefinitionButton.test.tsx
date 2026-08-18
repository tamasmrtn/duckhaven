import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, screen, waitFor } from "@tests/utils";

const SALES = "/acme-analytics/semantic/sales";

/**
 * Removing a definition is how a mistake gets fixed. The cases worth pinning are
 * the two where a plain delete would take something with it that the caller did
 * not ask to remove.
 */
describe("removing a definition", () => {
  it("removes a metric once the removal is confirmed", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: SALES });
    await user.click(await screen.findByRole("button", { name: /remove metric revenue/i }));

    await user.click(await screen.findByRole("button", { name: "Remove" }));

    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /remove metric revenue/i }),
      ).not.toBeInTheDocument(),
    );
  });

  it("does not remove anything until it is confirmed", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: SALES });
    await user.click(await screen.findByRole("button", { name: /remove metric revenue/i }));

    await user.click(await screen.findByRole("button", { name: "Cancel" }));

    expect(
      await screen.findByRole("button", { name: /remove metric revenue/i }),
    ).toBeVisible();
  });

  it("warns which metrics lose their time axis before a dimension goes", async () => {
    // The metric survives unbound rather than being deleted, which is the point
    // — but a metric no time filter can use is worth saying before the click.
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: SALES });
    await user.click(await screen.findByRole("tab", { name: /dimensions/i }));

    await user.click(
      await screen.findByRole("button", { name: /remove dimension event_time/i }),
    );

    expect(
      await screen.findByText(/measured on it and will be left without a time axis/i),
    ).toBeVisible();
  });

  it("surfaces the server's refusal naming what still binds a dataset", async () => {
    // The list of dependents is advice the client cannot invent, so it has to
    // arrive verbatim rather than as "could not delete".
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: SALES });
    await user.click(await screen.findByRole("tab", { name: /datasets/i }));
    await user.click(
      await screen.findByRole("button", { name: /remove dataset events/i }),
    );

    await user.click(await screen.findByRole("button", { name: "Remove" }));

    expect(await screen.findByText(/still has/i)).toBeVisible();
    expect(await screen.findByText(/metric 'revenue'/i)).toBeVisible();
  });
});
