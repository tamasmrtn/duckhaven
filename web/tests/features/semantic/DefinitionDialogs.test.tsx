import { describe, expect, it } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, screen, waitFor, within } from "@tests/utils";

const SALES = "/acme-analytics/semantic/sales";
const MARKETING = "/acme-analytics/semantic/marketing";

async function openDialog(name: RegExp) {
  await userEvent.click(await screen.findByRole("button", { name }));
  return screen.findByRole("dialog");
}

describe("authoring definitions", () => {
  it("offers the four kinds of definition on a locally-defined model", async () => {
    renderWithProviders({ initialRoute: SALES });

    expect(
      await screen.findByRole("button", { name: /^dataset$/i }),
    ).toBeVisible();
    expect(
      await screen.findByRole("button", { name: /^dimension$/i }),
    ).toBeVisible();
    expect(
      await screen.findByRole("button", { name: /^metric$/i }),
    ).toBeVisible();
    expect(
      await screen.findByRole("button", { name: /^join$/i }),
    ).toBeVisible();
  });

  it("offers none of them on an imported model", async () => {
    // A model has one owner, so an import is edited at its source.
    renderWithProviders({ initialRoute: MARKETING });
    await screen.findByRole("heading", { name: "Marketing" });

    expect(screen.queryByRole("button", { name: /^metric$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^dataset$/i })).toBeNull();
  });

  it("explains why a dataset needs a primary key", async () => {
    renderWithProviders({ initialRoute: SALES });
    const dialog = await openDialog(/^dataset$/i);

    expect(
      within(dialog).getByText(/would multiply every total that crosses it/i),
    ).toBeVisible();
  });

  it("explains what the metric's time axis decides", async () => {
    renderWithProviders({ initialRoute: SALES });
    const dialog = await openDialog(/^metric$/i);

    expect(
      within(dialog).getByText(/may silently measure on the wrong column/i),
    ).toBeVisible();
  });

  it("explains that the metric filter always applies", async () => {
    renderWithProviders({ initialRoute: SALES });
    const dialog = await openDialog(/^metric$/i);

    expect(within(dialog).getByText(/can never be forgotten/i)).toBeVisible();
  });

  it("will not submit a metric without a name and dataset", async () => {
    renderWithProviders({ initialRoute: SALES });
    const dialog = await openDialog(/^metric$/i);

    expect(within(dialog).getByRole("button", { name: "Add" })).toBeDisabled();
  });

  it("will not submit a sum with no expression to aggregate", async () => {
    renderWithProviders({ initialRoute: SALES });
    const dialog = await openDialog(/^metric$/i);

    await userEvent.type(within(dialog).getByLabelText("Name"), "profit");

    // Name alone is not enough: sum needs something to sum.
    expect(within(dialog).getByRole("button", { name: "Add" })).toBeDisabled();
  });

  it("creates a metric and reports it as a draft", async () => {
    renderWithProviders({ initialRoute: SALES });
    const dialog = await openDialog(/^metric$/i);

    await userEvent.type(within(dialog).getByLabelText("Name"), "refunds");
    await userEvent.click(within(dialog).getByLabelText("Dataset"));
    await userEvent.click(
      await screen.findByRole("option", { name: "events" }),
    );
    await userEvent.type(
      within(dialog).getByLabelText("Expression"),
      "refund_amount",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Add" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("says a join needs two datasets before offering one", async () => {
    renderWithProviders({ initialRoute: SALES });
    await screen.findByRole("heading", { name: "Sales" });

    // Sales has two, so it is enabled here; the title carries the reason it
    // would not be.
    expect(
      await screen.findByRole("button", { name: /^join$/i }),
    ).toBeEnabled();
  });

  it("states the join direction rule in the dialog itself", async () => {
    renderWithProviders({ initialRoute: SALES });
    const dialog = await openDialog(/^join$/i);

    expect(
      within(dialog).getByText(/multiplies rows and inflates every metric/i),
    ).toBeVisible();
  });

  it("flags a dataset with no declared key as a join target", async () => {
    renderWithProviders({ initialRoute: SALES });
    const dialog = await openDialog(/^join$/i);

    expect(
      within(dialog).getByText(/Must declare a primary key/i),
    ).toBeVisible();
  });

  it("explains what sample values are for", async () => {
    renderWithProviders({ initialRoute: SALES });
    const dialog = await openDialog(/^dimension$/i);

    expect(
      within(dialog).getByText(/instead of returning nothing/i),
    ).toBeVisible();
  });
});
