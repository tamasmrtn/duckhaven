import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse, delay } from "msw";
import { toast } from "sonner";
import { renderWithProviders } from "@tests/utils";
import { server } from "@tests/mock/server";
import { WORKSHEETS } from "@/mock/fixtures/worksheets";
import { AGENTS } from "@/mock/fixtures/agents";
import { QUERY_HISTORY, SAVED_QUERIES } from "@/mock/fixtures/queries";

// The test harness never mounts sonner's <Toaster>, so assert on the toast API.
vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

// Monaco does not run in jsdom. A textarea stands in for it here so typing,
// autosave and conflicts can be exercised end to end.
vi.mock("@monaco-editor/react", () => ({
  Editor: ({
    value,
    onChange,
  }: {
    value: string;
    onChange: (v: string) => void;
  }) => (
    <textarea
      aria-label="SQL editor"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
  default: () => null,
}));

const WS_ROUTE = "/acme-analytics/worksheets";

const sheet = (id: string) => WORKSHEETS.find((w) => w.id === id)!;

function doneQuery(id: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    workspace_id: "ws-1",
    agent_id: "ag-1",
    sql: "SELECT 1",
    status: "done",
    row_count: 1,
    duration_ms: 7,
    error: null,
    progress: null,
    started_at: "2026-05-15T10:00:00Z",
    finished_at: "2026-05-15T10:00:00.007Z",
    ...extra,
  };
}

afterEach(() => {
  // The active tab persists to sessionStorage, which the global afterEach
  // (localStorage only) does not clear.
  sessionStorage.clear();
});

describe("WorksheetPage tabs", () => {
  it("opens the worksheets saved on the server", async () => {
    renderWithProviders({ initialRoute: WS_ROUTE });
    expect(
      await screen.findByRole("tab", { name: /events\.sql/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /funnel-draft/ })).toBeInTheDocument();
    // A closed worksheet is not a tab.
    expect(screen.queryByRole("tab", { name: /retention scratch/ })).toBeNull();
  });

  it("marks only a linked worksheet whose SQL differs from its saved query", async () => {
    renderWithProviders({ initialRoute: WS_ROUTE });
    const funnel = await screen.findByRole("tab", { name: /funnel-draft/ });
    expect(
      within(funnel).getByLabelText("unsaved changes to saved query"),
    ).toBeInTheDocument();
    // Unlinked worksheets autosave, so there is nothing "unsaved" to flag.
    expect(
      within(screen.getByRole("tab", { name: /events\.sql/ })).queryByLabelText(
        "unsaved changes to saved query",
      ),
    ).toBeNull();
  });

  it("creates a worksheet named for when it was made", async () => {
    const user = userEvent.setup();
    const before = WORKSHEETS.length;
    renderWithProviders({ initialRoute: WS_ROUTE });
    await screen.findByRole("tab", { name: /events\.sql/ });

    await user.click(screen.getByLabelText("New worksheet"));

    await waitFor(() => expect(WORKSHEETS).toHaveLength(before + 1));
    const created = WORKSHEETS.at(-1)!;
    expect(created.title).not.toBe("untitled");
    const tab = await screen.findByRole("tab", { name: new RegExp(created.title) });
    await waitFor(() => expect(tab).toHaveAttribute("data-state", "active"));
  });

  it("keeps a closed worksheet, so it can be reopened later", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await screen.findByRole("tab", { name: /events\.sql/ });

    await user.click(screen.getByLabelText("Close events.sql"));

    expect(screen.queryByRole("tab", { name: /events\.sql/ })).toBeNull();
    await waitFor(() => expect(sheet("wk-1").is_open).toBe(false));
  });

  it("deletes a blank worksheet when it is closed", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await screen.findByRole("tab", { name: /events\.sql/ });
    await user.click(screen.getByLabelText("New worksheet"));
    await waitFor(() => expect(WORKSHEETS.at(-1)!.id).toMatch(/^wk-new/));
    const blank = WORKSHEETS.at(-1)!;

    await user.click(await screen.findByLabelText(`Close ${blank.title}`));

    await waitFor(() =>
      expect(WORKSHEETS.some((w) => w.id === blank.id)).toBe(false),
    );
  });

  it("renames a worksheet on double-click", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    const funnel = await screen.findByRole("tab", { name: /funnel-draft/ });

    await user.dblClick(funnel);
    const input = screen.getByRole("textbox", { name: "Rename worksheet" });
    await user.clear(input);
    await user.type(input, "my analysis");
    await user.keyboard("{Enter}");

    expect(
      await screen.findByRole("tab", { name: /my analysis/ }),
    ).toBeInTheDocument();
    await waitFor(() => expect(sheet("wk-2").title).toBe("my analysis"));
  });

  it("restores the active tab after navigating away and back", async () => {
    const user = userEvent.setup();
    const { router } = renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(await screen.findByRole("tab", { name: /funnel-draft/ }));

    await router.navigate({ to: "/acme-analytics/saved-queries" });
    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/acme-analytics/saved-queries"),
    );
    await router.navigate({ to: "/acme-analytics/worksheets" });

    const funnelTab = await screen.findByRole("tab", { name: /funnel-draft/ });
    await waitFor(() => expect(funnelTab).toHaveAttribute("data-state", "active"));
  });

  it("opens a freshly created workspace with one new worksheet", async () => {
    // The route guard requires the workspace to exist.
    server.use(
      http.get("/api/workspaces/qa-test-workspace", () =>
        HttpResponse.json({
          id: "ws-new",
          slug: "qa-test-workspace",
          name: "QA Test Workspace",
          storage_backend_id: "sb-1",
          storage_backend_kind: "object_store",
          created_at: new Date().toISOString(),
        }),
      ),
      http.get("/api/workspaces/qa-test-workspace/worksheets", () =>
        HttpResponse.json({ items: [], cursor: null, has_more: false }),
      ),
      http.post("/api/workspaces/qa-test-workspace/worksheets", async ({ request }) => {
        const body = (await request.json()) as { title: string };
        return HttpResponse.json(
          { ...sheet("wk-1"), id: "wk-qa", title: body.title, sql: "" },
          { status: 201 },
        );
      }),
    );
    renderWithProviders({ initialRoute: "/qa-test-workspace/worksheets" });

    // The strip holds just the new worksheet, and it is active.
    const [close] = await screen.findAllByLabelText(/^Close /);
    expect(screen.getAllByLabelText(/^Close /)).toHaveLength(1);
    expect(close.closest("[role=tab]")).toHaveAttribute("data-state", "active");
    expect(screen.queryByRole("tab", { name: /events\.sql/ })).toBeNull();
  });

  it("uploads tabs this browser kept locally before worksheets moved to the server", async () => {
    localStorage.setItem(
      "dh-worksheets-acme-analytics",
      JSON.stringify([
        { id: "tab-9", title: "old local work", sql: "SELECT 42", dirty: true },
      ]),
    );
    renderWithProviders({ initialRoute: WS_ROUTE });

    expect(
      await screen.findByRole("tab", { name: /old local work/ }),
    ).toBeInTheDocument();
    expect(WORKSHEETS.at(-1)).toMatchObject({ title: "old local work", sql: "SELECT 42" });
    expect(localStorage.getItem("dh-worksheets-acme-analytics")).toBeNull();
  });
});

describe("WorksheetPage tab accessibility", () => {
  it("does not nest a button inside the tab element", async () => {
    const { container } = renderWithProviders({ initialRoute: WS_ROUTE });
    await screen.findByRole("tab", { name: /events\.sql/ });
    expect(container.querySelector('button[role="tab"] button')).toBeNull();
    const tabEl = screen.getByRole("tab", { name: /events\.sql/ });
    expect(tabEl.tagName).not.toBe("BUTTON");
  });

  it("supports independent keyboard navigation, activation, and close", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    const eventsTab = await screen.findByRole("tab", { name: /events\.sql/ });
    const funnelTab = screen.getByRole("tab", { name: /funnel-draft/ });

    eventsTab.focus();
    await user.keyboard("{ArrowRight}");
    expect(funnelTab).toHaveFocus();
    await waitFor(() => expect(funnelTab).toHaveAttribute("data-state", "active"));

    const closeBtn = screen.getByLabelText("Close funnel-draft");
    closeBtn.focus();
    expect(closeBtn).toHaveFocus();
    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(screen.queryByRole("tab", { name: /funnel-draft/ })).toBeNull(),
    );
  });
});

describe("WorksheetPage autosave", () => {
  it("saves what is typed to the server", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    const editor = await screen.findByRole("textbox", { name: "SQL editor" });

    await user.clear(editor);
    await user.type(editor, "SELECT 7");

    await waitFor(() => expect(sheet("wk-1").sql).toBe("SELECT 7"), {
      timeout: 3000,
    });
    expect(sheet("wk-1").version).toBeGreaterThan(1);
  });

  // wk-1 is at version 1: made, never edited.
  it("shows no save status on a worksheet nobody has edited yet", async () => {
    renderWithProviders({ initialRoute: WS_ROUTE });
    await screen.findByRole("textbox", { name: "SQL editor" });

    expect(screen.queryByText("Saved")).toBeNull();
    expect(screen.queryByTitle("Worksheets save automatically")).toBeNull();
  });

  it("shows no save status on a new tab", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await screen.findByRole("tab", { name: /events\.sql/ });
    const before = WORKSHEETS.length;
    await user.click(screen.getByRole("button", { name: "New worksheet" }));

    await waitFor(() => expect(WORKSHEETS).toHaveLength(before + 1));
    const created = WORKSHEETS.at(-1)!;
    await waitFor(() =>
      expect(
        screen.getByRole("tab", { name: new RegExp(created.title) }),
      ).toHaveAttribute("data-state", "active"),
    );
    expect(screen.queryByText("Saved")).toBeNull();
  });

  it("shows the save status once the first edit is saved", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    const editor = await screen.findByRole("textbox", { name: "SQL editor" });

    await user.type(editor, " -- edit");

    expect(
      await screen.findByText("Saved", undefined, { timeout: 3000 }),
    ).toHaveAttribute("title", "Worksheets save automatically");
  });

  it("shows the save status on a worksheet edited before", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    // wk-2 is at version 3.
    await user.click(await screen.findByRole("tab", { name: /funnel-draft/ }));

    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("keeps an edit made just before leaving the page", async () => {
    const user = userEvent.setup();
    const { router } = renderWithProviders({ initialRoute: WS_ROUTE });
    const editor = await screen.findByRole("textbox", { name: "SQL editor" });
    await user.clear(editor);
    await user.type(editor, "SELECT 1 -- keep me");

    await router.navigate({ to: "/acme-analytics/catalog/acme_analytics/raw/events" });

    await waitFor(() => expect(sheet("wk-1").sql).toBe("SELECT 1 -- keep me"));
  });

  it("stops and asks when another window saved first", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    const editor = await screen.findByRole("textbox", { name: "SQL editor" });
    // Another window saves while this one still holds version 1.
    sheet("wk-1").sql = "SELECT 'from elsewhere'";
    sheet("wk-1").version = 5;

    await user.type(editor, " -- mine");

    const banner = await screen.findByText(/changed in another window/i, undefined, {
      timeout: 3000,
    });
    expect(sheet("wk-1").sql).toBe("SELECT 'from elsewhere'");

    await user.click(within(banner.closest("[role=alert]") as HTMLElement).getByRole("button", { name: "Load latest" }));
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "SQL editor" })).toHaveValue(
        "SELECT 'from elsewhere'",
      ),
    );
  });
});

describe("WorksheetPage run", () => {
  it("dispatches a query when Run is clicked and shows a status pill", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(await screen.findByRole("button", { name: /run query/i }));
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
  });

  it("double-clicking Run dispatches only one query", async () => {
    let postCount = 0;
    server.use(
      http.post("/api/workspaces/:ws/queries", async () => {
        postCount += 1;
        await delay(100);
        return HttpResponse.json({ id: "q-dbl", status: "queued" }, { status: 202 });
      }),
      http.get("/api/queries/q-dbl", () =>
        HttpResponse.json(
          doneQuery("q-dbl", { status: "running", progress: { stage: "scanning" } }),
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    const runBtn = await screen.findByRole("button", { name: /run query/i });

    await Promise.all([user.click(runBtn), user.click(runBtn)]);

    await screen.findByText("scanning", undefined, { timeout: 3000 });
    expect(postCount).toBe(1);
  });

  it("sends the worksheet's timeout in seconds", async () => {
    // Regression: the client sent `timeout`, which the API ignores.
    sheet("wk-1").timeout_s = 120;
    let body: Record<string, unknown> = {};
    server.use(
      http.post("/api/workspaces/:ws/queries", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: "q-t", status: "queued" }, { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(await screen.findByRole("button", { name: /run query/i }));

    await waitFor(() => expect(body.timeout_s).toBe(120));
    expect(body).not.toHaveProperty("timeout");
  });

  it("disables Run and explains when there are no agents at all", async () => {
    server.use(http.get("/api/agents", () => HttpResponse.json([])));
    renderWithProviders({ initialRoute: WS_ROUTE });

    const runBtn = await screen.findByRole("button", { name: /run query/i });
    expect(runBtn).toBeDisabled();
    expect(await screen.findByText(/no compute agents yet/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /add an agent/i })).toHaveAttribute(
      "href",
      "/acme-analytics/compute",
    );
  });

  it("does not claim there are no agents while they are still loading", async () => {
    server.use(
      http.get("/api/agents", async () => {
        await delay(400);
        return HttpResponse.json(AGENTS);
      }),
    );
    renderWithProviders({ initialRoute: WS_ROUTE });
    await screen.findByRole("tab", { name: /events\.sql/ });
    expect(screen.queryByText(/no compute agents yet/i)).toBeNull();
    expect(screen.queryByText(/no running agent/i)).toBeNull();
  });

  it("never lands on an offline agent just because it is listed first", async () => {
    // Regression: a warm cache defaulted the worksheet to agents[0], an offline
    // agent, so Run failed with "Agent not connected".
    server.use(
      http.get("/api/agents", () =>
        HttpResponse.json([
          AGENTS.find((a) => a.id === "ag-3"),
          ...AGENTS.filter((a) => a.id !== "ag-3"),
        ]),
      ),
    );
    const { router } = renderWithProviders({ initialRoute: WS_ROUTE });
    const picker = await screen.findByRole("combobox", { name: "Compute agent" });
    await waitFor(() => expect(picker).toHaveTextContent("agent-a"));

    await router.navigate({ to: "/acme-analytics/saved-queries" });
    await router.navigate({ to: "/acme-analytics/worksheets" });

    const again = await screen.findByRole("combobox", { name: "Compute agent" });
    await waitFor(() => expect(again).toHaveTextContent("agent-a"));
    expect(again).not.toHaveTextContent("agent-c");
  });

  it("remembers each worksheet's own agent", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    const picker = await screen.findByRole("combobox", { name: "Compute agent" });
    await user.click(picker);
    await user.click(await screen.findByText("agent-b"));
    await waitFor(() => expect(sheet("wk-1").agent_id).toBe("ag-2"));

    await user.click(screen.getByRole("tab", { name: /funnel-draft/ }));
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Compute agent" })).toHaveTextContent(
        "agent-a",
      ),
    );

    await user.click(screen.getByRole("tab", { name: /events\.sql/ }));
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Compute agent" })).toHaveTextContent(
        "agent-b",
      ),
    );
  });

  it("offers to start a stopped elastic agent when nothing is running", async () => {
    server.use(
      http.get("/api/agents", () =>
        HttpResponse.json([
          {
            ...AGENTS.find((a) => a.id === "ag-5"),
            status: "unavailable",
            lifecycle: "terminated",
            capabilities: null,
          },
        ]),
      ),
    );
    renderWithProviders({ initialRoute: WS_ROUTE });

    const run = await screen.findByRole("button", {
      name: /start agent and run query/i,
    });
    expect(run).toHaveTextContent("Start & run");
    expect(run).toBeEnabled();
  });

  it("shows an unreachable agent's error with a way to switch agents", async () => {
    server.use(
      http.post("/api/workspaces/:ws/queries", () =>
        HttpResponse.json(
          {
            error: "unavailable",
            message: "Agent not connected",
            details: { query_id: "q-down" },
          },
          { status: 503 },
        ),
      ),
      http.get("/api/queries/q-down", () =>
        HttpResponse.json(
          doneQuery("q-down", { status: "failed", error: "Agent not connected" }),
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(await screen.findByRole("button", { name: /run query/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Agent not connected");
    await user.click(screen.getByRole("button", { name: /switch agent/i }));
    expect(await screen.findByPlaceholderText("Search agents…")).toBeInTheDocument();
    // The failed run is the worksheet's last run, as History records it.
    await waitFor(() => expect(sheet("wk-1").last_query_id).toBe("q-down"));
  });

  it("surfaces a rejected dispatch as a readable error", async () => {
    server.use(
      http.post("/api/workspaces/:ws/queries", () =>
        HttpResponse.json(
          {
            error: "sql_not_allowed",
            message: "Disallowed statement type(s): SET",
            details: null,
          },
          { status: 422 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(await screen.findByRole("button", { name: /run query/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Disallowed statement type(s): SET");
    expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
  });

  it("opens the assistant with the failed query prefilled, without sending it", async () => {
    server.use(
      http.post("/api/workspaces/:ws/queries", () =>
        HttpResponse.json(
          {
            error: "sql_not_allowed",
            message: "Disallowed statement type(s): SET",
            details: null,
          },
          { status: 422 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(await screen.findByRole("button", { name: /run query/i }));
    await screen.findByRole("alert");

    await user.click(screen.getByRole("button", { name: /fix with assistant/i }));

    const panel = await screen.findByRole("complementary", { name: "AI assistant" });
    const composer = within(panel).getByLabelText("Message") as HTMLTextAreaElement;
    expect(composer.value).toContain("Fix this query error");
    expect(composer.value).toContain("FROM raw.events");
    expect(composer.value).toContain("Disallowed statement type(s): SET");
    expect(composer).toHaveFocus();
  });

  it("shows the running progress stage in the results header", async () => {
    server.use(
      http.post("/api/workspaces/:ws/queries", () =>
        HttpResponse.json({ id: "q-prog", status: "queued" }, { status: 202 }),
      ),
      http.get("/api/queries/:id", () =>
        HttpResponse.json(
          doneQuery("q-prog", { status: "running", progress: { stage: "scanning" } }),
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(await screen.findByRole("button", { name: /run query/i }));
    expect(await screen.findByText("scanning")).toBeInTheDocument();
  });

  it("runs a multi-statement worksheet sequentially, one dispatch at a time", async () => {
    sheet("wk-1").sql = "CREATE TABLE t AS SELECT 1; SELECT 2";
    const dispatched: string[] = [];
    server.use(
      http.post("/api/workspaces/:ws/queries", async ({ request }) => {
        const body = (await request.json()) as { sql: string };
        dispatched.push(body.sql);
        return HttpResponse.json(
          { id: `q-${dispatched.length}`, status: "queued" },
          { status: 202 },
        );
      }),
      http.get("/api/queries/:id", ({ params }) =>
        HttpResponse.json(doneQuery(params.id as string)),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(await screen.findByRole("button", { name: /run query/i }));

    await waitFor(() => expect(dispatched).toHaveLength(2));
    expect(dispatched).toEqual(["CREATE TABLE t AS SELECT 1", "SELECT 2"]);
    expect(await screen.findByText(/statement 2\/2/i)).toBeInTheDocument();
  });

  it("halts the sequence when a statement does not complete", async () => {
    sheet("wk-1").sql = "CREATE TABLE t AS SELECT 1; SELECT 2";
    let posts = 0;
    server.use(
      http.post("/api/workspaces/:ws/queries", () => {
        posts += 1;
        return HttpResponse.json({ id: `q-${posts}`, status: "queued" }, { status: 202 });
      }),
      http.get("/api/queries/:id", ({ params }) =>
        HttpResponse.json(
          doneQuery(params.id as string, { status: "failed", error: "boom" }),
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(await screen.findByRole("button", { name: /run query/i }));

    expect(await screen.findByText("boom")).toBeInTheDocument();
    expect(posts).toBe(1);
  });
});

describe("WorksheetPage results", () => {
  it("keeps results with the worksheet that ran them", async () => {
    // Regression: results were page-wide, so a new or neighbouring tab showed
    // the previous tab's rows.
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(await screen.findByRole("button", { name: /run query/i }));
    await screen.findByRole("status");

    await user.click(screen.getByRole("tab", { name: /funnel-draft/ }));
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(screen.getByText("No results yet")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /events\.sql/ }));
    expect(await screen.findByRole("status")).toBeInTheDocument();
  });

  it("starts a new worksheet with no results", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(await screen.findByRole("button", { name: /run query/i }));
    await screen.findByRole("status");

    await user.click(screen.getByLabelText("New worksheet"));

    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(screen.getByText("No results yet")).toBeInTheDocument();
  });

  it("restores a worksheet's last results after a reload", async () => {
    sheet("wk-1").last_query_id = "q-persisted";
    server.use(
      http.get("/api/queries/q-persisted", () =>
        HttpResponse.json(doneQuery("q-persisted", { result_bytes: 2_097_152 })),
      ),
    );
    renderWithProviders({ initialRoute: WS_ROUTE });

    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    expect(await screen.findByText("2.0 MB")).toBeInTheDocument();
  });

  it("explains results that have expired on the agent", async () => {
    sheet("wk-1").last_query_id = "q-old";
    server.use(
      http.get("/api/queries/q-old", () => HttpResponse.json(doneQuery("q-old"))),
      http.get("/api/queries/q-old/rows", () =>
        HttpResponse.json(
          { error: "gone", message: "Result expired", details: null },
          { status: 410 },
        ),
      ),
    );
    renderWithProviders({ initialRoute: WS_ROUTE });

    expect(await screen.findByRole("alert")).toHaveTextContent(/expired/i);
  });

  it("forgets a last run that no longer exists", async () => {
    sheet("wk-1").last_query_id = "q-pruned";
    renderWithProviders({ initialRoute: WS_ROUTE });
    await screen.findByRole("tab", { name: /events\.sql/ });

    await waitFor(() => expect(sheet("wk-1").last_query_id).toBeNull());
  });

  it("flags results that came from an earlier version of the SQL", async () => {
    const hist = QUERY_HISTORY.find((q) => q.status === "done")!;
    sheet("wk-1").last_query_id = hist.id;
    sheet("wk-1").sql = "SELECT something_else";
    renderWithProviders({ initialRoute: WS_ROUTE });

    expect(
      await screen.findByText("From an earlier version of this query"),
    ).toBeInTheDocument();
  });
});

describe("WorksheetPage catalog and worksheet rail", () => {
  it("does not overwrite the worksheet when a catalog table is clicked", async () => {
    localStorage.setItem(
      "dh-tree-expanded-acme-analytics",
      JSON.stringify(["c:acme_analytics", "s:acme_analytics.raw"]),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    const before = sheet("wk-1").sql;

    await user.click(await screen.findByRole("button", { name: /^events/i }));

    expect(sheet("wk-1").sql).toBe(before);
    expect(screen.getByRole("textbox", { name: "SQL editor" })).toHaveValue(before);
  });

  it("does not render information_schema view rows as disabled", async () => {
    localStorage.setItem(
      "dh-tree-expanded-acme-analytics",
      JSON.stringify(["c:acme_analytics"]),
    );
    renderWithProviders({ initialRoute: WS_ROUTE });
    await userEvent.click(
      await screen.findByRole("button", { name: /information_schema/i }),
    );
    expect(screen.getByRole("button", { name: "tables" })).not.toBeDisabled();
  });

  it("lists closed worksheets and reopens one as a tab", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(
      within(await screen.findByRole("group", { name: "Sidebar" })).getByRole(
        "button",
        { name: "Worksheets" },
      ),
    );

    const list = await screen.findByRole("list", { name: "My worksheets" });
    await user.click(within(list).getByRole("button", { name: /^retention scratch/ }));

    const tab = await screen.findByRole("tab", { name: /retention scratch/ });
    await waitFor(() => expect(tab).toHaveAttribute("data-state", "active"));
    await waitFor(() => expect(sheet("wk-3").is_open).toBe(true));
  });

  it("opens a shared saved query from the rail", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(
      within(await screen.findByRole("group", { name: "Sidebar" })).getByRole(
        "button",
        { name: "Worksheets" },
      ),
    );

    const shared = await screen.findByRole("list", { name: "Shared saved queries" });
    await user.click(within(shared).getByRole("button", { name: /Daily events/ }));

    expect(await screen.findByRole("tab", { name: /Daily events/ })).toBeInTheDocument();
    expect(WORKSHEETS.at(-1)).toMatchObject({
      title: "Daily events",
      saved_query_id: "sq-1",
    });
  });
});

describe("WorksheetPage sidebar layout", () => {
  // Regression: the switch sat in the sidebar's own header, a different
  // height from the toolbar beside it, and a 4px resize column opened a gap
  // between the two.
  it("puts the sidebar switch in the tab row, in a cell as wide as the sidebar", async () => {
    renderWithProviders({ initialRoute: WS_ROUTE });
    const group = await screen.findByRole("group", { name: "Sidebar" });
    const cell = screen.getByTestId("rail-switch-cell");

    expect(cell).toContainElement(group);
    expect(cell.parentElement).toContainElement(
      screen.getByRole("tab", { name: /events\.sql/ }),
    );
    expect(cell.style.width).toBe(screen.getByTestId("worksheet-rail").style.width);
    // The switch appears once, not again inside the sidebar.
    expect(screen.getAllByRole("group", { name: "Sidebar" })).toHaveLength(1);
  });

  it("overlays the resize handle instead of giving it a column", async () => {
    renderWithProviders({ initialRoute: WS_ROUTE });
    const rail = await screen.findByTestId("worksheet-rail");
    const handle = screen.getByTestId("rail-resize-handle");

    expect(rail).toContainElement(handle);
    expect(handle.className).toContain("absolute");
  });

  it("resizes the switch cell with the sidebar", async () => {
    renderWithProviders({ initialRoute: WS_ROUTE });
    const handle = await screen.findByTestId("rail-resize-handle");

    fireEvent.mouseDown(handle, { clientX: 280 });
    fireEvent.mouseMove(window, { clientX: 340 });
    fireEvent.mouseUp(window);

    await waitFor(() =>
      expect(screen.getByTestId("worksheet-rail").style.width).toBe("340px"),
    );
    expect(screen.getByTestId("rail-switch-cell").style.width).toBe("340px");
  });

  it("remembers the chosen sidebar view", async () => {
    const user = userEvent.setup();
    const { unmount } = renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(
      within(await screen.findByRole("group", { name: "Sidebar" })).getByRole(
        "button",
        { name: "Worksheets" },
      ),
    );
    expect(await screen.findByRole("list", { name: "My worksheets" })).toBeInTheDocument();
    unmount();

    renderWithProviders({ initialRoute: WS_ROUTE });
    expect(await screen.findByRole("list", { name: "My worksheets" })).toBeInTheDocument();
  });
});

describe("WorksheetPage responsive", () => {
  const originalMatchMedia = window.matchMedia;
  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  function mockViewport(isMobile: boolean) {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: isMobile,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as unknown as typeof window.matchMedia;
  }

  it("shows a sidebar drawer trigger on narrow screens", async () => {
    mockViewport(true);
    renderWithProviders({ initialRoute: WS_ROUTE });
    expect(await screen.findByRole("button", { name: /show tables/i })).toBeInTheDocument();
  });

  it("keeps the sidebar switch inside the drawer on narrow screens", async () => {
    mockViewport(true);
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await screen.findByRole("tab", { name: /events\.sql/ });
    expect(screen.queryByTestId("rail-switch-cell")).toBeNull();

    await user.click(screen.getByRole("button", { name: /show tables/i }));
    const drawer = await screen.findByRole("dialog");
    await user.click(
      within(within(drawer).getByRole("group", { name: "Sidebar" })).getByRole(
        "button",
        { name: "Worksheets" },
      ),
    );
    expect(
      await within(drawer).findByRole("list", { name: "My worksheets" }),
    ).toBeInTheDocument();
  });

  it("renders the inline sidebar without a drawer trigger on wide screens", async () => {
    mockViewport(false);
    renderWithProviders({ initialRoute: WS_ROUTE });
    await screen.findByRole("tab", { name: /events\.sql/ });
    expect(screen.queryByRole("button", { name: /show tables/i })).toBeNull();
  });

  it("wraps the results header row instead of clipping at narrow widths", async () => {
    renderWithProviders({ initialRoute: WS_ROUTE });
    const resultsTabBtn = await screen.findByRole("tab", { name: /^results$/i });
    const header = resultsTabBtn.parentElement?.parentElement;
    expect(header?.className).toContain("flex-wrap");
  });
});

describe("WorksheetPage save", () => {
  it("saves a new query from the worksheet and links the two", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await screen.findByRole("tab", { name: /events\.sql/ });

    await user.click(screen.getByRole("button", { name: /save…/i }));
    const dialog = screen.getByRole("dialog");
    const name = within(dialog).getByPlaceholderText(/my query name/i);
    // Prefilled from the worksheet's name.
    expect(name).toHaveValue("events.sql");
    await user.clear(name);
    await user.type(name, "Daily report");
    await user.click(within(dialog).getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByRole("tab", { name: /Daily report/ })).toBeInTheDocument();
    const saved = SAVED_QUERIES.find((q) => q.name === "Daily report")!;
    await waitFor(() => expect(sheet("wk-1").saved_query_id).toBe(saved.id));
  });

  it("asks before replacing a shared query with the same name", async () => {
    const user = userEvent.setup();
    const { router } = renderWithProviders({ initialRoute: WS_ROUTE });
    await screen.findByRole("tab", { name: /events\.sql/ });

    await user.click(screen.getByRole("button", { name: /save…/i }));
    const dialog = screen.getByRole("dialog");
    const name = within(dialog).getByPlaceholderText(/my query name/i);
    await user.clear(name);
    await user.type(name, "daily EVENTS");
    await user.click(within(dialog).getByRole("button", { name: /^save$/i }));

    // Regression: saving used to overwrite a colleague's query by name silently.
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(/already exists/);
    expect(SAVED_QUERIES.find((q) => q.id === "sq-1")!.sql).not.toBe(sheet("wk-1").sql);

    await user.click(within(dialog).getByRole("button", { name: /^replace$/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await router.navigate({ to: "/acme-analytics/saved-queries" });
    await screen.findByText("Funnel overview");
    expect(screen.getAllByText(/daily events/i)).toHaveLength(1);
  });

  it("saves a linked worksheet in place, without a dialog", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: `${WS_ROUTE}?tab=wk-2` });
    const funnel = await screen.findByRole("tab", { name: /funnel-draft/ });
    await waitFor(() => expect(funnel).toHaveAttribute("data-state", "active"));

    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(SAVED_QUERIES.find((q) => q.id === "sq-2")!.sql).toBe(sheet("wk-2").sql),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(toast.success).toHaveBeenCalledWith(expect.stringMatching(/Funnel overview/));
    await waitFor(() =>
      expect(within(funnel).queryByLabelText("unsaved changes to saved query")).toBeNull(),
    );
  });

  it("reverts a linked worksheet to its saved SQL", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: `${WS_ROUTE}?tab=wk-2` });
    await screen.findByRole("tab", { name: /funnel-draft/ });

    await user.click(screen.getByRole("button", { name: /more save options/i }));
    await user.click(await screen.findByRole("menuitem", { name: /revert to saved/i }));

    const saved = SAVED_QUERIES.find((q) => q.id === "sq-2")!.sql;
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: "SQL editor" })).toHaveValue(saved),
    );
  });

  it("shows an error toast and keeps the dialog open when the save fails", async () => {
    vi.mocked(toast.error).mockClear();
    server.use(
      http.post("/api/workspaces/:ws/saved-queries", () =>
        HttpResponse.json({ detail: "Save exploded" }, { status: 500 }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });
    await screen.findByRole("tab", { name: /events\.sql/ });

    await user.click(screen.getByRole("button", { name: /save…/i }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});

describe("save as metric", () => {
  it("says what to select rather than opening an empty form", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: WS_ROUTE });

    await user.click(await screen.findByRole("button", { name: /save as metric/i }));

    expect(toast.info).toHaveBeenCalledWith(
      expect.stringMatching(/select the expression first/i),
    );
  });
});

describe("WorksheetPage tab deep-linking", () => {
  it("activates the worksheet named by ?tab= on load", async () => {
    renderWithProviders({ initialRoute: `${WS_ROUTE}?tab=wk-2` });
    const funnelTab = await screen.findByRole("tab", { name: /funnel-draft/ });
    await waitFor(() => expect(funnelTab).toHaveAttribute("data-state", "active"));
  });

  it("updates the URL search param when the user switches tabs", async () => {
    const user = userEvent.setup();
    const { router } = renderWithProviders({ initialRoute: WS_ROUTE });
    await user.click(await screen.findByRole("tab", { name: /funnel-draft/ }));
    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({ tab: "wk-2" }),
    );
  });

  it("falls back to the first tab when ?tab= names an unknown worksheet", async () => {
    renderWithProviders({ initialRoute: `${WS_ROUTE}?tab=does-not-exist` });
    const eventsTab = await screen.findByRole("tab", { name: /events\.sql/ });
    await waitFor(() => expect(eventsTab).toHaveAttribute("data-state", "active"));
  });

  it("reopens a closed worksheet named by ?tab=", async () => {
    renderWithProviders({ initialRoute: `${WS_ROUTE}?tab=wk-3` });
    const tab = await screen.findByRole("tab", { name: /retention scratch/ });
    await waitFor(() => expect(tab).toHaveAttribute("data-state", "active"));
    expect(sheet("wk-3").is_open).toBe(true);
  });

});
