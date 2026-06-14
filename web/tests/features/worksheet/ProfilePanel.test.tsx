import { describe, it, expect } from "vitest";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { render, screen, waitFor } from "@tests/utils";
import { createWrapper } from "@tests/utils";
import { server } from "@tests/mock/server";
import { ProfilePanel } from "@/features/worksheet/profile/ProfilePanel";
import { SAMPLE_PROFILE } from "@/mock/fixtures/queries";

function renderPanel(queryId = "q-1", enabled = true) {
  const { wrapper } = createWrapper();
  return render(<ProfilePanel queryId={queryId} enabled={enabled} />, { wrapper });
}

describe("ProfilePanel", () => {
  it("renders the summary strip and operator tree from a profile", async () => {
    server.use(
      http.get("/api/queries/:id/profile", () => HttpResponse.json(SAMPLE_PROFILE)),
    );
    renderPanel();

    expect(await screen.findByText("Latency")).toBeInTheDocument();
    expect(screen.getByText("Peak memory")).toBeInTheDocument();
    // Operator types from the tree.
    expect(screen.getByText("ORDER_BY")).toBeInTheDocument();
    expect(screen.getByText("HASH_GROUP_BY")).toBeInTheDocument();
  });

  it("flags spill and scan blow-up inefficiencies", async () => {
    server.use(
      http.get("/api/queries/:id/profile", () => HttpResponse.json(SAMPLE_PROFILE)),
    );
    renderPanel();

    // Query-level spill banner.
    expect(await screen.findByText(/Spilled to disk/i)).toBeInTheDocument();
    // Per-node scan blow-up badge (events scans 2M rows for 30 returned).
    expect(screen.getByText(/Scan blow-up/i)).toBeInTheDocument();
  });

  it("collapses and expands a node's children", async () => {
    server.use(
      http.get("/api/queries/:id/profile", () => HttpResponse.json(SAMPLE_PROFILE)),
    );
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText("ORDER_BY");
    expect(screen.getByText("HASH_GROUP_BY")).toBeInTheDocument();

    // Collapsing the root hides its descendants.
    await user.click(screen.getAllByLabelText("Collapse")[0]);
    await waitFor(() =>
      expect(screen.queryByText("HASH_GROUP_BY")).not.toBeInTheDocument(),
    );
  });

  it("shows a no-profile state when the profile is null", async () => {
    server.use(http.get("/api/queries/:id/profile", () => HttpResponse.json(null)));
    renderPanel();
    expect(await screen.findByText(/No profile for this query/i)).toBeInTheDocument();
  });

  it("shows a waiting state until the query is done", () => {
    renderPanel("q-1", false);
    expect(
      screen.getByText(/profile appears once the query finishes/i),
    ).toBeInTheDocument();
  });
});
