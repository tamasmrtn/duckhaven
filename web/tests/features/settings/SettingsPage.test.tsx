import { afterEach, describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "@tests/utils";
import { server } from "@tests/mock/server";
import { CURRENT_USER, currentUser } from "@/mock/fixtures/users";

const ROUTE = "/acme-analytics/settings";
const TZ = process.env.TZ;

// Records the body of every workspace PATCH, answering with the workspace as
// the mock API stores it.
function recordPatches() {
  const sent: Record<string, unknown>[] = [];
  server.events.on("request:start", async ({ request }) => {
    if (request.method === "PATCH" && request.url.includes("/api/workspaces/"))
      sent.push((await request.clone().json()) as Record<string, unknown>);
  });
  return sent;
}

afterEach(() => {
  process.env.TZ = TZ;
  server.events.removeAllListeners();
});

describe("SettingsPage — Workspace tab", () => {
  // Regression: the tabs sat in the page body, unlike every other page,
  // which puts them in the toolbar row under the header.
  it("puts the Workspace | Account tabs in the toolbar row", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByLabelText("Name");

    const toolbar = screen.getByRole("tablist").parentElement!;
    expect(toolbar).toHaveClass("border-b");
    expect(within(toolbar).queryByLabelText("Name")).not.toBeInTheDocument();
  });

  it("shows the current name and description, and saves an update", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    const nameInput = (await screen.findByLabelText(
      "Name",
    )) as HTMLInputElement;
    expect(nameInput.value).toBe("acme-analytics");

    await user.clear(nameInput);
    await user.type(nameInput, "Renamed workspace");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => expect(nameInput.value).toBe("Renamed workspace"));
  });

  it("turns the result cache off for the workspace", async () => {
    let sent: Record<string, unknown> | null = null;
    server.use(
      http.patch("/api/workspaces/:ws", async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          id: "ws-1",
          slug: "acme-analytics",
          name: "acme-analytics",
          description: null,
          default_catalog: "acme_analytics",
          storage_backend_id: "sb-1",
          storage_backend_kind: "s3",
          result_cache_enabled: false,
          created_at: "2026-01-01T00:00:00Z",
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    const toggle = await screen.findByRole("checkbox", {
      name: /answer repeated queries from the result cache/i,
    });
    expect(toggle).toBeChecked();
    await user.click(toggle);
    await waitFor(() => expect(sent).toEqual({ result_cache_enabled: false }));
  });

  it("says which way the result cache toggle goes, not just 'turn it off'", async () => {
    const ws = {
      id: "ws-1",
      slug: "acme-analytics",
      name: "acme-analytics",
      description: null,
      default_catalog: "acme_analytics",
      storage_backend_id: "sb-1",
      storage_backend_kind: "s3",
      created_at: "2026-01-01T00:00:00Z",
    };
    server.use(
      http.get("/api/workspaces/:ws", () =>
        HttpResponse.json({ ...ws, result_cache_enabled: false }),
      ),
    );
    renderWithProviders({ initialRoute: ROUTE });
    expect(
      await screen.findByText(/turn this on to answer repeated queries/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/turn this off to run every query/i)).toBeNull();
  });

  it("offers to turn it off while the cache is on", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    expect(
      await screen.findByText(/turn this off to run every query/i),
    ).toBeInTheDocument();
  });

  it("sets the default compute from the agents the owner can use", async () => {
    let sent: Record<string, unknown> | null = null;
    server.use(
      http.patch("/api/workspaces/:ws", async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          id: "ws-1",
          slug: "acme-analytics",
          name: "acme-analytics",
          description: null,
          default_catalog: "acme_analytics",
          storage_backend_id: "sb-1",
          storage_backend_kind: "s3",
          default_agent_id: "ag-1",
          default_agent: {
            id: "ag-1",
            name: "agent-a",
            provider: null,
            lifecycle: null,
            status: "healthy",
          },
          created_at: "2026-01-01T00:00:00Z",
        });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    const select = (await screen.findByLabelText(
      "Default compute",
    )) as HTMLSelectElement;
    expect(select.value).toBe("");
    await waitFor(() =>
      expect(
        within(select).getByRole("option", { name: /agent-a/ }),
      ).toBeInTheDocument(),
    );
    await user.selectOptions(select, "ag-1");

    await waitFor(() => expect(sent).toEqual({ default_agent_id: "ag-1" }));
  });

  it("clears the default compute with an explicit null", async () => {
    let sent: Record<string, unknown> | null = null;
    server.use(
      http.get("/api/workspaces/:ws", () =>
        HttpResponse.json({
          id: "ws-1",
          slug: "acme-analytics",
          name: "acme-analytics",
          description: null,
          default_catalog: "acme_analytics",
          storage_backend_id: "sb-1",
          storage_backend_kind: "s3",
          default_agent_id: "ag-1",
          default_agent: {
            id: "ag-1",
            name: "agent-a",
            provider: null,
            lifecycle: null,
            status: "healthy",
          },
          created_at: "2026-01-01T00:00:00Z",
        }),
      ),
      http.patch("/api/workspaces/:ws", async ({ request }) => {
        sent = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ id: "ws-1", slug: "acme-analytics" });
      }),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    const select = (await screen.findByLabelText(
      "Default compute",
    )) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("ag-1"));
    await user.selectOptions(select, "");

    await waitFor(() => expect(sent).toEqual({ default_agent_id: null }));
  });

  it("shows a default the owner has no grant on instead of hiding it", async () => {
    server.use(
      http.get("/api/workspaces/:ws", () =>
        HttpResponse.json({
          id: "ws-1",
          slug: "acme-analytics",
          name: "acme-analytics",
          description: null,
          default_catalog: "acme_analytics",
          storage_backend_id: "sb-1",
          storage_backend_kind: "s3",
          default_agent_id: "not-in-my-list",
          default_agent: {
            id: "not-in-my-list",
            name: "team-restricted",
            provider: null,
            lifecycle: null,
            status: "healthy",
          },
          created_at: "2026-01-01T00:00:00Z",
        }),
      ),
    );
    renderWithProviders({ initialRoute: ROUTE });

    const select = (await screen.findByLabelText(
      "Default compute",
    )) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("not-in-my-list"));
    expect(
      within(select).getByRole("option", { name: "team-restricted" }),
    ).toBeInTheDocument();
  });

  it("shows the workspace's time zone and what today is there", async () => {
    server.use(
      http.get("/api/workspaces/:ws", () =>
        HttpResponse.json({
          id: "ws-1",
          slug: "acme-analytics",
          name: "acme-analytics",
          description: null,
          default_catalog: "acme_analytics",
          storage_backend_id: "sb-1",
          storage_backend_kind: "s3",
          timezone: "Pacific/Kiritimati",
          created_at: "2026-01-01T00:00:00Z",
        }),
      ),
    );
    renderWithProviders({ initialRoute: ROUTE });

    const picker = await screen.findByRole("combobox", { name: "Time zone" });
    expect(picker).toHaveTextContent("Pacific/Kiritimati");
    expect(picker).toHaveTextContent("UTC+14:00");
    const today = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Pacific/Kiritimati",
      weekday: "short",
      day: "numeric",
      month: "short",
      year: "numeric",
    })
      .format(new Date())
      .replace(",", "");
    expect(
      screen.getByText(new RegExp(`Today here is ${today}\\.`)),
    ).toBeVisible();
  });

  it("sets the time zone by searching for a city", async () => {
    const sent = recordPatches();
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    const picker = await screen.findByRole("combobox", { name: "Time zone" });
    expect(picker).toHaveTextContent("UTC");
    await user.click(picker);
    await user.type(screen.getByPlaceholderText(/search a city/i), "kolkata");
    await user.click(await screen.findByRole("option", { name: /Kolkata/ }));

    await waitFor(() => expect(sent).toEqual([{ timezone: "Asia/Kolkata" }]));
    await waitFor(() => expect(picker).toHaveTextContent("Asia/Kolkata"));
  });

  // Regression: cmdk's letter-by-letter match put America/Kentucky/Louisville
  // and America/North_Dakota/Beulah above Kolkata, so Enter saved one of them.
  it("saves the zone a search names when Enter is pressed", async () => {
    process.env.TZ = "UTC";
    const sent = recordPatches();
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    await user.click(
      await screen.findByRole("combobox", { name: "Time zone" }),
    );
    await user.type(
      screen.getByPlaceholderText(/search a city/i),
      "kolkata{Enter}",
    );

    await waitFor(() => expect(sent).toEqual([{ timezone: "Asia/Kolkata" }]));
  });

  it("finds a time zone by its offset", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    await user.click(
      await screen.findByRole("combobox", { name: "Time zone" }),
    );
    await user.type(screen.getByPlaceholderText(/search a city/i), "+5:45");
    expect(
      await screen.findByRole("option", { name: /Kathmandu/ }),
    ).toBeVisible();
    expect(
      screen.queryByRole("option", { name: /Amsterdam/ }),
    ).not.toBeInTheDocument();
  });

  it("offers the browser's time zone first, by its current name", async () => {
    process.env.TZ = "Asia/Calcutta";
    const sent = recordPatches();
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    await user.click(
      await screen.findByRole("combobox", { name: "Time zone" }),
    );
    const suggested = screen.getByRole("group", { name: "Your browser" });
    await user.click(
      within(suggested).getByRole("option", { name: /Kolkata/ }),
    );

    await waitFor(() => expect(sent).toEqual([{ timezone: "Asia/Kolkata" }]));
  });

  it("sends nothing when the current time zone is picked again", async () => {
    process.env.TZ = "UTC";
    const sent = recordPatches();
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    const picker = await screen.findByRole("combobox", { name: "Time zone" });
    await user.click(picker);
    expect(
      screen.queryByRole("group", { name: "Your browser" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: /^UTC/ }));

    expect(picker).toHaveAttribute("aria-expanded", "false");
    expect(sent).toEqual([]);
  });

  it("requires typing the workspace name to confirm delete", async () => {
    const user = userEvent.setup();
    const { router } = renderWithProviders({ initialRoute: ROUTE });
    await screen.findByLabelText("Name");

    await user.click(screen.getByRole("button", { name: /delete workspace/i }));
    const dialog = await screen.findByRole("dialog");
    const confirmBtn = within(dialog).getByRole("button", {
      name: /^delete workspace$/i,
    });
    expect(confirmBtn).toBeDisabled();

    await user.type(
      within(dialog).getByLabelText(/type .* to confirm/i),
      "acme-analytics",
    );
    expect(confirmBtn).not.toBeDisabled();
    await user.click(confirmBtn);

    await waitFor(() =>
      expect(router.state.location.pathname).toBe("/welcome"),
    );
  });

  it("disables rename and delete for a non-owner", async () => {
    server.use(
      http.get("/api/me", () =>
        HttpResponse.json({ ...CURRENT_USER, id: "u-3" }),
      ),
    );
    renderWithProviders({ initialRoute: ROUTE });

    const nameInput = await screen.findByLabelText("Name");
    expect(nameInput).toBeDisabled();
    expect(
      screen.getByRole("button", { name: /delete workspace/i }),
    ).toBeDisabled();
    expect(await screen.findByLabelText("Default compute")).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "Time zone" })).toBeDisabled();
    expect(
      screen.getByText("Only a workspace owner can change the time zone."),
    ).toBeVisible();
  });
});

describe("SettingsPage — Account tab", () => {
  it("shows profile info", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByLabelText("Name");

    await user.click(screen.getByRole("tab", { name: /account/i }));

    // "Marton" also appears in the TopBar's own user-menu trigger — scope to
    // the Account tab panel so this only asserts on the profile section.
    const panel = await screen.findByRole("tabpanel");
    expect(
      await within(panel).findByText(CURRENT_USER.email),
    ).toBeInTheDocument();
    expect(within(panel).getByText(CURRENT_USER.name)).toBeInTheDocument();
  });

  it("saves the theme on the account and keeps the top-bar toggle in sync", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByLabelText("Name");
    await user.click(screen.getByRole("tab", { name: /account/i }));

    const darkOption = await screen.findByRole("radio", { name: /dark/i });
    await user.click(darkOption);

    await waitFor(() =>
      expect(darkOption).toHaveAttribute("aria-checked", "true"),
    );
    await waitFor(() => expect(currentUser().theme).toBe("dark"));
    // The top-bar theme dropdown trigger is mounted at the same time (same
    // AppShell) — its icon must reflect the same ThemeProvider state.
    expect(
      screen
        .getByRole("button", { name: /change theme/i })
        .querySelector("svg.lucide-moon"),
    ).toBeInTheDocument();
  });

  describe("radio keys", () => {
    async function openThemeGroup() {
      const user = userEvent.setup();
      renderWithProviders({ initialRoute: ROUTE });
      await screen.findByLabelText("Name");
      await user.click(screen.getByRole("tab", { name: /account/i }));
      const group = await screen.findByRole("radiogroup", { name: "Theme" });
      return { user, group };
    }

    it("puts only the chosen option in the tab order", async () => {
      const { group } = await openThemeGroup();

      const tabbable = within(group)
        .getAllByRole("radio")
        .filter((radio) => radio.tabIndex === 0);
      expect(tabbable).toEqual([
        within(group).getByRole("radio", { name: "System" }),
      ]);
    });

    it("moves and chooses with the arrow keys, wrapping at the ends", async () => {
      const { user, group } = await openThemeGroup();
      const radio = (name: string) =>
        within(group).getByRole("radio", { name });
      radio("System").focus();

      await user.keyboard("{ArrowRight}");
      expect(radio("Light")).toHaveFocus();
      expect(radio("Light")).toHaveAttribute("aria-checked", "true");

      await user.keyboard("{ArrowLeft}");
      expect(radio("System")).toHaveFocus();

      await user.keyboard("{ArrowUp}");
      expect(radio("Dark")).toHaveFocus();
      expect(radio("Dark")).toHaveAttribute("aria-checked", "true");
      expect(radio("Dark").tabIndex).toBe(0);

      await user.keyboard("{ArrowDown}");
      expect(radio("System")).toHaveAttribute("aria-checked", "true");
    });

    it("chooses a navigation layout with the arrow keys", async () => {
      const user = userEvent.setup();
      renderWithProviders({ initialRoute: ROUTE });
      await screen.findByLabelText("Name");
      await user.click(screen.getByRole("tab", { name: /account/i }));
      const group = await screen.findByRole("radiogroup", {
        name: "Navigation",
      });
      within(group).getByRole("radio", { name: "Expanded" }).focus();

      await user.keyboard("{ArrowRight}");

      expect(
        within(group).getByRole("radio", { name: "Collapsed" }),
      ).toHaveFocus();
      expect(
        screen.getByRole("navigation", { name: "Main navigation" }),
      ).toHaveAttribute("data-mode", "collapsed");
      await waitFor(() => expect(currentUser().sidebar).toBe("collapsed"));
    });
  });

  describe("navigation layout", () => {
    async function openAccountTab() {
      const user = userEvent.setup();
      renderWithProviders({ initialRoute: ROUTE });
      await screen.findByLabelText("Name");
      await user.click(screen.getByRole("tab", { name: /account/i }));
      const group = await screen.findByRole("radiogroup", {
        name: "Navigation",
      });
      return { user, group };
    }

    it("is expanded for a user who never chose", async () => {
      const { group } = await openAccountTab();

      expect(
        within(group).getByRole("radio", { name: "Expanded" }),
      ).toHaveAttribute("aria-checked", "true");
    });

    it("lays the navigation out as chosen and saves it on the account", async () => {
      const { user, group } = await openAccountTab();

      await user.click(within(group).getByRole("radio", { name: "Collapsed" }));

      expect(
        screen.getByRole("navigation", { name: "Main navigation" }),
      ).toHaveAttribute("data-mode", "collapsed");
      await waitFor(() => expect(currentUser().sidebar).toBe("collapsed"));
    });

    it("follows Cmd+B", async () => {
      const { user, group } = await openAccountTab();

      await user.keyboard("{Control>}b{/Control}");

      expect(
        within(group).getByRole("radio", { name: "Collapsed" }),
      ).toHaveAttribute("aria-checked", "true");
    });
  });
});
