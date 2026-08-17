import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, screen } from "@tests/utils";

const SALES = "/acme-analytics/semantic/sales";
const MARKETING = "/acme-analytics/semantic/marketing";

describe("SemanticModelDetail", () => {
  it("shows what a metric actually computes", async () => {
    renderWithProviders({ initialRoute: SALES });

    expect(
      await screen.findByText("SUM(amount) FILTER (WHERE event_type = 'purchase')"),
    ).toBeVisible();
  });

  it("names the column a metric is measured on", async () => {
    // The field that decides whether "revenue last month" is right, so it is
    // shown next to the calculation rather than buried.
    renderWithProviders({ initialRoute: SALES });

    // One per metric, so all three are expected.
    expect(await screen.findAllByText("Measured on")).toHaveLength(3);
    expect(await screen.findAllByText("event_time")).not.toHaveLength(0);
  });

  it("says plainly when a metric has no time axis", async () => {
    renderWithProviders({ initialRoute: MARKETING });

    expect(
      await screen.findByText(/time filters cannot use this metric/i),
    ).toBeVisible();
  });

  it("shows a metric's caveat with the definition", async () => {
    renderWithProviders({ initialRoute: SALES });

    expect(
      await screen.findByText("Excludes internal test accounts."),
    ).toBeVisible();
  });

  it("shows the SQL a definition compiles to", async () => {
    renderWithProviders({ initialRoute: SALES });

    expect(await screen.findAllByText(/SELECT/)).not.toHaveLength(0);
  });

  it("explains why a broken definition is broken", async () => {
    renderWithProviders({ initialRoute: MARKETING });

    expect(
      await screen.findByText(/no longer exist: cost_usd/i),
    ).toBeVisible();
  });

  it("warns that a draft is not used by the assistant", async () => {
    renderWithProviders({ initialRoute: MARKETING });

    expect(
      await screen.findByText(/will not use its\s+definitions until an owner publishes it/i),
    ).toBeVisible();
  });

  it("says an imported model is edited at its source", async () => {
    renderWithProviders({ initialRoute: MARKETING });

    expect(await screen.findAllByText(/Imported from/i)).not.toHaveLength(0);
  });

  it("does not offer Publish on an already-published model", async () => {
    renderWithProviders({ initialRoute: SALES });
    await screen.findByRole("heading", { name: "Sales" });

    expect(screen.queryByRole("button", { name: /^publish$/i })).toBeNull();
    // Validate stays available: a published model can still rot.
    expect(screen.getByRole("button", { name: /validate/i })).toBeVisible();
  });

  it("refuses to publish a model whose definitions do not resolve", async () => {
    renderWithProviders({ initialRoute: MARKETING });

    await userEvent.click(await screen.findByRole("button", { name: /publish/i }));

    // Inline, not a toast: this is a list somebody has to read and act on.
    expect(
      await screen.findByText(/cannot be published until its definitions resolve/i),
    ).toBeVisible();
  });

  it("reports validation failures with the reason", async () => {
    renderWithProviders({ initialRoute: MARKETING });

    await userEvent.click(await screen.findByRole("button", { name: /validate/i }));

    expect(await screen.findByText(/Validation found 1 problem/i)).toBeVisible();
    expect(
      await screen.findAllByText(/no longer exist: cost_usd/i),
    ).not.toHaveLength(0);
  });

  it("shows the declared join and its direction", async () => {
    renderWithProviders({ initialRoute: SALES });
    await userEvent.click(await screen.findByRole("tab", { name: /joins/i }));

    expect(
      await screen.findByText(/events → users \(many_to_one\)/),
    ).toBeVisible();
  });

  it("shows a dimension's sample values", async () => {
    // So a filter is written against what is stored rather than what was said.
    renderWithProviders({ initialRoute: SALES });
    await userEvent.click(await screen.findByRole("tab", { name: /dimensions/i }));

    expect(await screen.findByText(/free, pro, enterprise/)).toBeVisible();
  });

  it("shows which physical table a dataset binds to", async () => {
    renderWithProviders({ initialRoute: SALES });
    await userEvent.click(await screen.findByRole("tab", { name: /datasets/i }));

    expect(
      await screen.findByText("acme_analytics.raw.events"),
    ).toBeVisible();
  });
});
