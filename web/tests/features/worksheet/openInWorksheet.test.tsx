import { describe, it, expect, vi } from "vitest";
import { act, render, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { createTestQueryClient } from "@tests/utils";
import { WORKSHEETS } from "@/mock/fixtures/worksheets";
import {
  useOpenInWorksheet,
  type OpenInWorksheetRequest,
} from "@/features/worksheet/openInWorksheet";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// A two-route router: somewhere to start, and the worksheet route to land on.
function setup() {
  let open: (req: OpenInWorksheetRequest) => Promise<void> = async () => {};
  function Probe() {
    open = useOpenInWorksheet("acme-analytics");
    return null;
  }
  const root = createRootRoute();
  const start = createRoute({ getParentRoute: () => root, path: "/", component: Probe });
  const worksheets = createRoute({
    getParentRoute: () => root,
    path: "/$ws/worksheets",
    validateSearch: (s: Record<string, unknown>) => ({ tab: s.tab as string | undefined }),
  });
  const router = createRouter({
    routeTree: root.addChildren([start, worksheets]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, open: (req: OpenInWorksheetRequest) => act(() => open(req)) };
}

describe("useOpenInWorksheet", () => {
  it("creates a worksheet for new SQL and navigates to it", async () => {
    const { router, open } = setup();
    await waitFor(() => expect(router.state.status).toBe("idle"));

    await open({ sql: "SELECT 1", title: "events" });

    const created = WORKSHEETS.at(-1)!;
    expect(created).toMatchObject({ title: "events", sql: "SELECT 1", is_open: true });
    expect(router.state.location.search).toMatchObject({ tab: created.id });
  });

  it("focuses the worksheet already linked to a saved query", async () => {
    const { router, open } = setup();
    await waitFor(() => expect(router.state.status).toBe("idle"));
    const before = WORKSHEETS.length;

    await open({ sql: "…", title: "Funnel overview", savedQueryId: "sq-2" });

    expect(WORKSHEETS).toHaveLength(before);
    expect(router.state.location.search).toMatchObject({ tab: "wk-2" });
  });

  it("reopens a closed linked worksheet", async () => {
    WORKSHEETS.find((w) => w.id === "wk-3")!.saved_query_id = "sq-1";
    const { router, open } = setup();
    await waitFor(() => expect(router.state.status).toBe("idle"));

    await open({ sql: "…", title: "Daily events", savedQueryId: "sq-1" });

    expect(WORKSHEETS.find((w) => w.id === "wk-3")!.is_open).toBe(true);
    expect(router.state.location.search).toMatchObject({ tab: "wk-3" });
  });

  it("still opens when the saved default agent is not usable by this user", async () => {
    const { router, open } = setup();
    await waitFor(() => expect(router.state.status).toBe("idle"));
    const { server } = await import("@tests/mock/server");
    const { http, HttpResponse } = await import("msw");
    let attempts = 0;
    server.use(
      http.post("/api/workspaces/:ws/worksheets", async ({ request }) => {
        attempts += 1;
        const body = (await request.json()) as { agent_id: string | null };
        if (body.agent_id) {
          return HttpResponse.json(
            { error: "not_found", message: "Agent not found", details: null },
            { status: 404 },
          );
        }
        return HttpResponse.json({ ...WORKSHEETS[0], id: "wk-agentless" }, { status: 201 });
      }),
    );

    await open({ sql: "SELECT 1", title: "x", agentId: "ag-hidden" });

    expect(attempts).toBe(2);
    expect(router.state.location.search).toMatchObject({ tab: "wk-agentless" });
  });
});
