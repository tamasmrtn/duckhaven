import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "@tests/utils";
import { server } from "@tests/mock/server";
import { CURRENT_USER } from "@/mock/fixtures/users";

const ROUTE = "/acme-analytics/settings";

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

  it("keeps the theme control in sync with the top-bar toggle", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByLabelText("Name");
    await user.click(screen.getByRole("tab", { name: /account/i }));

    const darkOption = await screen.findByRole("radio", { name: /dark/i });
    await user.click(darkOption);

    await waitFor(() =>
      expect(darkOption).toHaveAttribute("aria-checked", "true"),
    );
    // The top-bar theme dropdown trigger is mounted at the same time (same
    // AppShell) — its icon must reflect the same ThemeProvider state.
    expect(
      screen
        .getByRole("button", { name: /change theme/i })
        .querySelector("svg.lucide-moon"),
    ).toBeInTheDocument();
  });
});
