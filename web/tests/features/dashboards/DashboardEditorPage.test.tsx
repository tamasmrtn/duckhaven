import { beforeEach, describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { server } from "@tests/mock/server";
import {
  configure,
  fireEvent,
  renderWithProviders,
  screen,
  waitFor,
  within,
} from "@tests/utils";
import { toast } from "sonner";
import { DASHBOARDS, DRAFTS } from "@/mock/fixtures/dashboards";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// Monaco does not run in jsdom: a textarea stands in for the editor, and the
// YAML language is reported loaded.
vi.mock("@monaco-editor/react", () => ({
  Editor: ({
    value,
    onChange,
    options,
  }: {
    value: string;
    onChange: (v: string) => void;
    options?: { readOnly?: boolean };
  }) => (
    <textarea
      aria-label="Dashboard file"
      value={value}
      readOnly={options?.readOnly}
      onChange={(e) => onChange(e.target.value)}
    />
  ),
  default: () => null,
}));
vi.mock("@/features/dashboards/editor/yamlLoader", () => ({
  useDashboardYamlReady: () => true,
}));

// The file is checked once typing pauses, and the preview then runs.
configure({ asyncUtilTimeout: 4000 });

// Each edit waits out that pause (SETTLE_MS) before its preview runs, and a
// test that makes several edits spends seconds in those pauses alone. Under
// coverage on CI that passed vitest's 5s default.
const SLOW = { timeout: 15_000 };

const ROUTE = "/acme-analytics/dashboards";
const PATCH_URL = "/api/workspaces/:ws/dashboards/:name";
const PUBLISH_URL = "/api/workspaces/:ws/dashboards/:name/publish";

function file() {
  return screen.getByRole("textbox", { name: "Dashboard file" });
}

function value() {
  return (file() as HTMLTextAreaElement).value;
}

function type(text: string) {
  fireEvent.change(file(), { target: { value: text } });
}

/** The preview has run the file in the editor. */
async function previewed() {
  const preview = await screen.findByRole("region", { name: "Preview" });
  await within(preview).findByText(/answered in/);
  return preview;
}

function record(url: string, method: "patch" | "post") {
  const bodies: Record<string, unknown>[] = [];
  server.use(
    http[method](url, async ({ request }) => {
      bodies.push((await request.clone().json()) as Record<string, unknown>);
    }),
  );
  return bodies;
}

describe("DashboardEditorPage", SLOW, () => {
  beforeEach(() => vi.mocked(toast.success).mockClear());

  it("opens the draft beside a preview that runs it as you", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });

    await screen.findByRole("textbox", { name: "Dashboard file" });
    expect(value()).toContain("by country");
    expect(screen.getByText(/Editing the draft\./)).toBeVisible();
    const preview = await previewed();
    expect(within(preview).getByText("Preview, viewing as you")).toBeVisible();
    expect(
      await within(preview).findByRole("region", { name: "Revenue by country" }),
    ).toBeVisible();
    expect(await screen.findByText(/No problems/)).toBeVisible();
  });

  it("marks what is wrong with the file, and keeps the last preview", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });
    const preview = await previewed();

    type("version: 1\ntitle: [unclosed\n");

    const problems = await screen.findByRole("region", { name: "Problems" });
    expect(await within(problems).findByText("1 error, 0 warnings")).toBeVisible();
    expect(within(problems).getByText("Line 3")).toBeVisible();
    expect(
      await within(preview).findByText(/Showing the last file that loaded/),
    ).toBeVisible();
    expect(within(preview).getByRole("region", { name: "Revenue by country" }))
      .toBeVisible();
  });

  it("saves a draft against the version it was made from", async () => {
    const saves = record(PATCH_URL, "patch");
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });
    await screen.findByRole("textbox", { name: "Dashboard file" });
    const save = screen.getByRole("button", { name: "Save draft" });
    expect(save).toBeDisabled();

    const edited = value().replace(
      "title: Sales overview",
      "title: Sales at a glance",
    );
    type(edited);
    expect(screen.getByText("Unsaved changes")).toBeVisible();
    await userEvent.click(save);

    await waitFor(() => expect(saves).toHaveLength(1));
    expect(saves[0]).toEqual({ source_text: edited, base_version: 3 });
    expect(await screen.findByText(/Draft saved just now from the app/))
      .toBeVisible();
    expect(save).toBeDisabled();
  });

  it("offers to load a change saved meanwhile, or to save over it", async () => {
    const saves = record(PATCH_URL, "patch");
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });
    await screen.findByRole("textbox", { name: "Dashboard file" });
    // Someone else saves the draft while this editor holds version 3.
    const sales = DASHBOARDS.find((d) => d.name === "sales-overview")!;
    sales.version = 4;
    sales.updated_via = "cli";
    DRAFTS["sales-overview"].spec = {
      ...DRAFTS["sales-overview"].spec,
      title: "Their title",
    };

    type(`${value()}# mine\n`);
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));

    const dialog = await screen.findByRole("dialog", {
      name: "The dashboard changed while you edited it",
    });
    expect(dialog).toHaveTextContent("from the CLI, and is at version 4");
    expect(dialog).toHaveTextContent("made against version 3");
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Save mine over it" }),
    );

    await waitFor(() => expect(saves).toHaveLength(2));
    expect(saves[1]).toMatchObject({ base_version: 4 });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Draft saved"),
    );
    expect(value()).toContain("# mine");
  });

  it("loads the other version, when that is the one to keep", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });
    await screen.findByRole("textbox", { name: "Dashboard file" });
    DASHBOARDS.find((d) => d.name === "sales-overview")!.version = 4;
    DRAFTS["sales-overview"].spec = {
      ...DRAFTS["sales-overview"].spec,
      title: "Their title",
    };

    type(`${value()}# mine\n`);
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Load their version" }),
    );

    expect(value()).toContain("title: Their title");
    expect(value()).not.toContain("# mine");
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
  });

  it("publishes with a note of what changed, and lists it among the revisions", async () => {
    const publishes = record(PUBLISH_URL, "post");
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });
    await screen.findByRole("textbox", { name: "Dashboard file" });

    await userEvent.click(await screen.findByRole("button", { name: "Publish" }));
    const dialog = await screen.findByRole("dialog", { name: "Publish dashboard" });
    await userEvent.type(
      within(dialog).getByLabelText("What changed (optional)"),
      "Describe the country filter",
    );
    await userEvent.click(within(dialog).getByRole("button", { name: "Publish" }));

    await waitFor(() => expect(publishes).toHaveLength(1));
    expect(publishes[0]).toEqual({
      base_version: 3,
      message: "Describe the country filter",
    });
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith("Published", expect.anything()),
    );
    expect(screen.queryByText(/Editing the draft\./)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Revisions" }));
    const sheet = await screen.findByRole("dialog", { name: "Revisions" });
    expect(await within(sheet).findByText("Describe the country filter"))
      .toBeVisible();
    expect(within(sheet).getByText(/Revision 3/)).toHaveTextContent(
      "what readers see",
    );
  });

  it("saves unsaved changes before it publishes them", async () => {
    const saves = record(PATCH_URL, "patch");
    const publishes = record(PUBLISH_URL, "post");
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });
    await screen.findByRole("textbox", { name: "Dashboard file" });
    type(`${value()}# one more\n`);

    await userEvent.click(await screen.findByRole("button", { name: "Publish" }));
    const dialog = await screen.findByRole("dialog", { name: "Publish dashboard" });
    expect(dialog).toHaveTextContent("Your unsaved changes are saved to the draft first.");
    await userEvent.click(within(dialog).getByRole("button", { name: "Publish" }));

    await waitFor(() => expect(publishes).toHaveLength(1));
    expect(saves).toHaveLength(1);
    // Published against the version the save made.
    expect(publishes[0]).toMatchObject({ base_version: 4 });
  });

  it("gets out of the way of the problems when publishing is refused", async () => {
    server.use(
      http.post(PUBLISH_URL, () =>
        HttpResponse.json(
          {
            error: "invalid_dashboard",
            message: "Model 'sales' has no metric 'revenu'.",
            details: { diagnostics: [] },
          },
          { status: 422 },
        ),
      ),
    );
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });
    await screen.findByRole("textbox", { name: "Dashboard file" });

    await userEvent.click(await screen.findByRole("button", { name: "Publish" }));
    const dialog = await screen.findByRole("dialog", { name: "Publish dashboard" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Publish" }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        "Model 'sales' has no metric 'revenu'.",
        { description: "The problems are listed under the editor." },
      ),
    );
    expect(
      screen.queryByRole("dialog", { name: "Publish dashboard" }),
    ).not.toBeInTheDocument();
  });

  it("puts an earlier revision in the editor, to save and publish again", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });
    await screen.findByRole("textbox", { name: "Dashboard file" });
    expect(value()).toContain("country:");

    await userEvent.click(screen.getByRole("button", { name: "Revisions" }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Open revision 1 in the editor" }),
    );

    // Revision 1 had no country filter.
    await waitFor(() =>
      expect(value()).not.toContain("country:"),
    );
    expect(screen.getByText("Unsaved changes")).toBeVisible();
    expect(toast.success).toHaveBeenCalledWith(
      "Revision 1 is in the editor",
      expect.anything(),
    );
  });

  it("discards the draft, going back to the published file", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });
    await screen.findByRole("textbox", { name: "Dashboard file" });

    await userEvent.click(screen.getByRole("button", { name: "Discard draft" }));
    const dialog = await screen.findByRole("dialog", { name: "Discard the draft?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Discard" }));

    await waitFor(() =>
      expect(value()).toContain("refreshed from the orders table"),
    );
    expect(screen.queryByRole("button", { name: "Discard draft" })).not
      .toBeInTheDocument();
    expect(screen.getByText("No changes since it was published")).toBeVisible();
  });

  it("moves widgets and rows, and sets widths, by changing the file", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });
    await previewed();
    const before = value();
    expect(before.indexOf("id: revenue")).toBeLessThan(before.indexOf("id: orders"));

    await userEvent.click(
      await screen.findByRole("button", { name: "Arrange Revenue" }),
    );
    await userEvent.click(screen.getByRole("menuitem", { name: "Move right" }));
    const moved = value();
    expect(moved.indexOf("id: orders")).toBeLessThan(moved.indexOf("id: revenue"));

    // The controls wait for the preview to hold the file as it stands.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Arrange Revenue" })).toBeEnabled(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Arrange Revenue" }));
    const half = screen.getByRole("menuitemradio", { name: "Half (6)" });
    expect(half).not.toHaveAttribute("data-disabled");
    await userEvent.click(half);
    expect(value()).toMatch(/id: revenue[\s\S]*width: 6/);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Move row 1 down" })).toBeEnabled(),
    );
    await userEvent.click(screen.getByRole("button", { name: "Move row 1 down" }));
    const rows = value();
    expect(rows.indexOf("id: revenue_trend")).toBeLessThan(rows.indexOf("id: revenue\n"));
  });

  it("only reads a dashboard managed in git", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/finance-weekly/edit` });

    expect(await screen.findByRole("textbox", { name: "Dashboard file" }))
      .toHaveAttribute("readonly");
    expect(screen.getByText(/so the editor only reads it/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Save draft" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Publish" })).not.toBeInTheDocument();
    await previewed();
    expect(screen.queryByRole("button", { name: /^Arrange/ })).not.toBeInTheDocument();
  });

  it("says that editing needs the writer role", async () => {
    server.use(
      http.get("/api/workspaces/:ws/dashboards/:name", () =>
        HttpResponse.json(
          {
            error: "forbidden",
            message: "Reading a draft needs the writer role.",
            details: null,
          },
          { status: 403 },
        ),
      ),
    );
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });

    expect(
      await screen.findByText("Editing a dashboard needs the writer role"),
    ).toBeVisible();
  });

  it("asks before leaving with changes nobody saved", async () => {
    const { router } = renderWithProviders({
      initialRoute: `${ROUTE}/sales-overview/edit`,
    });
    await screen.findByRole("textbox", { name: "Dashboard file" });
    type(`${value()}# unsaved\n`);

    await userEvent.click(screen.getByRole("link", { name: "Done" }));
    const dialog = await screen.findByRole("dialog", { name: "Leave without saving?" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Stay" }));
    expect(router.state.location.pathname).toBe(`${ROUTE}/sales-overview/edit`);

    await userEvent.click(screen.getByRole("link", { name: "Done" }));
    await userEvent.click(
      within(await screen.findByRole("dialog", { name: "Leave without saving?" }))
        .getByRole("button", { name: "Leave" }),
    );
    await waitFor(() =>
      expect(router.state.location.pathname).toBe(`${ROUTE}/sales-overview`),
    );
  });

  it("follows a dashboard nobody published to its new name", async () => {
    const { router } = renderWithProviders({
      initialRoute: `${ROUTE}/churn-review/edit`,
    });
    await screen.findByRole("textbox", { name: "Dashboard file" });
    expect(screen.getByText(/Nobody has published this dashboard/)).toBeVisible();

    type(
      value().replace(
        "name: churn-review",
        "name: churn-weekly",
      ),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));

    await waitFor(() =>
      expect(router.state.location.pathname).toBe(`${ROUTE}/churn-weekly/edit`),
    );
    expect(value()).toContain("name: churn-weekly");
  });
});
