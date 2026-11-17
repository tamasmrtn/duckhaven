import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { renderWithProviders, screen, waitFor, within } from "@tests/utils";
import { dashboardsApi } from "@/api/dashboards";
import { DASHBOARDS, PROPOSALS } from "@/mock/fixtures/dashboards";

// Accepting opens the dashboard's editor. Monaco does not run in jsdom: a
// textarea stands in for it, and the YAML language is reported loaded.
vi.mock("@monaco-editor/react", () => ({
  Editor: ({ value }: { value: string }) => (
    <textarea aria-label="Dashboard file" value={value} readOnly />
  ),
  default: () => null,
}));
vi.mock("@/features/dashboards/editor/yamlLoader", () => ({
  useDashboardYamlReady: () => true,
}));

const SLOW = { timeout: 15_000 };
const WS = "acme-analytics";

function open(id: string) {
  return renderWithProviders({
    initialRoute: `/${WS}/dashboard-proposals/${id}`,
  });
}

async function heading(title: string) {
  return screen.findByRole("heading", { name: `Proposed changes to ${title}` });
}

describe("DashboardProposalPage", SLOW, () => {
  it("lists the changes and runs the file they make, as you", async () => {
    open("proposal-1");

    expect(await heading("Sales overview")).toBeVisible();
    expect(
      screen.getByText("Retitle the dashboard for its weekly review."),
    ).toBeVisible();
    const changes = screen.getByRole("region", { name: "Changes" });
    expect(
      within(changes).getByText(
        "Title changed: “Sales overview” → “Sales, weekly”",
      ),
    ).toBeVisible();
    const preview = await screen.findByRole("region", { name: "Preview" });
    expect(within(preview).getByText("Preview, viewing as you")).toBeVisible();
    expect(
      await within(preview).findByRole("region", { name: "Revenue" }),
    ).toBeVisible();
    // Nothing on the page edits the file.
    expect(screen.queryByRole("button", { name: /Arrange/ })).toBeNull();
  });

  it("saves the file as the draft when accepted, and opens the editor", async () => {
    const user = userEvent.setup();
    const { router } = open("proposal-1");
    await heading("Sales overview");

    await user.click(screen.getByRole("button", { name: "Accept as draft" }));

    await waitFor(() =>
      expect(router.state.location.pathname).toBe(
        `/${WS}/dashboards/sales-overview/edit`,
      ),
    );
    const draft = await dashboardsApi.get(WS, "sales-overview", true);
    expect(draft.spec.title).toBe("Sales, weekly");
    expect(draft.updated_via).toBe("assistant");
    // Readers still see the published file until an owner publishes.
    const published = await dashboardsApi.get(WS, "sales-overview");
    expect(published.spec.title).toBe("Sales overview");
    expect((await dashboardsApi.getProposal(WS, "proposal-1")).status).toBe(
      "accepted",
    );
  });

  it("leaves the dashboard as it was when discarded", async () => {
    const user = userEvent.setup();
    open("proposal-1");
    await heading("Sales overview");

    await user.click(screen.getByRole("button", { name: "Discard" }));

    expect(
      await screen.findByText(/You discarded these changes/),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Accept as draft" }),
    ).toBeNull();
    const draft = await dashboardsApi.get(WS, "sales-overview", true);
    expect(draft.spec.title).toBe("Sales overview");
  });

  it("will not accept changes the dashboard has moved on from", async () => {
    DASHBOARDS.find((d) => d.name === "sales-overview")!.version += 1;
    open("proposal-1");

    await heading("Sales overview");

    expect(
      screen.getByText(
        /The dashboard changed after these changes were proposed/,
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Accept as draft" }),
    ).toBeNull();
    expect(screen.getByRole("button", { name: "Discard" })).toBeVisible();
  });

  it("says why a stale proposal was refused when accepting it", async () => {
    const user = userEvent.setup();
    open("proposal-1");
    await heading("Sales overview");
    // Someone saves the dashboard while the page is open.
    DASHBOARDS.find((d) => d.name === "sales-overview")!.version += 1;

    await user.click(screen.getByRole("button", { name: "Accept as draft" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "changed after this proposal was made",
    );
    expect(
      await screen.findByText(/accepting them would undo that/),
    ).toBeVisible();
  });

  it("offers the file to copy for a dashboard managed in git", async () => {
    open("proposal-git");

    await heading("Finance weekly");

    expect(screen.getByText(/managed in git/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Copy file" })).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Accept as draft" }),
    ).toBeNull();
  });

  it("says when the changes have expired", async () => {
    PROPOSALS["proposal-1"].expires_at = new Date(Date.now() - 1).toISOString();
    open("proposal-1");

    await heading("Sales overview");

    expect(screen.getByText(/These changes expired a day after/)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Accept as draft" }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
  });

  it("makes accepting the page's one brand-yellow action", async () => {
    open("proposal-1");
    await heading("Sales overview");

    expect(screen.getByRole("button", { name: "Accept as draft" })).toHaveClass(
      "bg-[var(--brand-yellow)]",
    );
    expect(screen.getByRole("button", { name: "Discard" })).not.toHaveClass(
      "bg-[var(--brand-yellow)]",
    );
  });

  it("says how to get changes that are gone", async () => {
    open("gone");

    expect(await screen.findByText("These changes are gone")).toBeVisible();
    expect(screen.getByText(/A proposal lasts a day/)).toBeVisible();
  });
});
