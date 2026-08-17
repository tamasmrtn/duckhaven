import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { server } from "@tests/mock/server";
import { renderWithProviders, screen, waitFor } from "@tests/utils";

const ROUTE = "/acme-analytics/semantic";
const MODELS_URL = "/api/workspaces/:ws/semantic/models";

describe("SemanticPage", () => {
  it("lists the workspace's semantic models", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    expect(await screen.findByText("Sales")).toBeVisible();
    expect(await screen.findByText("Marketing")).toBeVisible();
  });

  it("shows publishing state, because that is what the assistant reads", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    expect(await screen.findByText("published")).toBeVisible();
    expect(await screen.findByText("draft")).toBeVisible();
  });

  it("marks an imported model by its source rather than as locally defined", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    expect(await screen.findByText("dbt")).toBeVisible();
    expect(await screen.findByText("Defined here")).toBeVisible();
  });

  it("surfaces broken definitions in the list", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    expect(await screen.findByText(/1 definition broken/i)).toBeVisible();
  });

  it("offers to create the first model when there are none", async () => {
    server.use(http.get(MODELS_URL, () => HttpResponse.json([])));
    renderWithProviders({ initialRoute: ROUTE });

    expect(await screen.findByText(/No semantic models yet/i)).toBeVisible();
    expect(
      await screen.findByRole("button", { name: /create the first one/i }),
    ).toBeVisible();
  });

  it("navigates to a model when its row is clicked", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    await userEvent.click(await screen.findByText("Sales"));

    expect(await screen.findByRole("tab", { name: /metrics/i })).toBeVisible();
  });

  it("creates a model and opens it", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    await userEvent.click(await screen.findByRole("button", { name: /new model/i }));
    await userEvent.type(await screen.findByLabelText(/identifier/i), "support");
    await userEvent.type(await screen.findByLabelText(/display name/i), "Support");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(async () =>
      expect(await screen.findByRole("tab", { name: /metrics/i })).toBeVisible(),
    );
  });

  it("keeps the dialog open when the identifier is already taken", async () => {
    // The failure surfaces as a toast, which this harness does not mount; what
    // is worth pinning here is that a rejected create does not navigate away and
    // silently look like it worked.
    renderWithProviders({ initialRoute: ROUTE });

    await userEvent.click(await screen.findByRole("button", { name: /new model/i }));
    await userEvent.type(await screen.findByLabelText(/identifier/i), "sales");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Create" })).toBeVisible(),
    );
    expect(screen.queryByRole("tab", { name: /metrics/i })).toBeNull();
  });
});
