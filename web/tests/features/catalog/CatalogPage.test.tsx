import { describe, it, expect, afterEach, beforeEach } from "vitest";
import { screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@tests/mock/server";
import { renderWithProviders } from "@tests/utils";
import { recordRecentlyViewed } from "@/utils/recentlyViewed";
import { WORKSHEETS } from "@/mock/fixtures/worksheets";

const CATALOG_ROUTE = "/acme-analytics/catalog";

describe("CatalogPage", () => {
  // The tree starts collapsed and remembers what was opened: begin with the
  // default catalog and its `raw` schema open.
  beforeEach(() => {
    localStorage.setItem(
      "dh-tree-expanded-acme-analytics",
      JSON.stringify(["c:acme_analytics", "s:acme_analytics.raw"]),
    );
  });

  it("creates a schema via the catalog right-click menu", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: CATALOG_ROUTE });

    // The MSW fixture seeds raw + analytics under ws-1's default catalog.
    await screen.findByText("raw");

    // "Create schema" lives on the catalog node's context menu.
    const catalogNode = await screen.findByRole("button", {
      name: /acme_analytics/i,
    });
    fireEvent.contextMenu(catalogNode);
    await user.click(
      await screen.findByRole("menuitem", { name: /create schema/i }),
    );
    await user.type(await screen.findByLabelText(/^name$/i), "gold");
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() => {
      expect(screen.getByText("gold")).toBeInTheDocument();
    });
  });

  it("creates a table via the schema right-click menu", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: CATALOG_ROUTE });

    const rawRow = await screen.findByRole("button", { name: /raw/i });
    fireEvent.contextMenu(rawRow);
    await user.click(
      await screen.findByRole("menuitem", { name: /create table/i }),
    );
    await user.type(await screen.findByLabelText(/^name$/i), "pageviews");
    await user.type(screen.getByLabelText(/column name/i), "id");
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    // `raw` is expanded, so the new table appears on refetch.
    await waitFor(() => {
      expect(screen.getByText("pageviews")).toBeInTheDocument();
    });
  });

  it("recounts a table via the table right-click menu", async () => {
    let recounted = "";
    server.use(
      http.post(
        "/api/workspaces/:ws/catalogs/:catalog/schemas/:schema/tables/:table/recount",
        ({ params }) => {
          recounted = `${params.schema}.${params.table}`;
          return HttpResponse.json({ row_count: 7 });
        },
      ),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: CATALOG_ROUTE });

    // Wait for the shallower "raw" schema node before reaching for "events"
    // (nested a level deeper, under its own table fetch) — splitting the
    // wait keeps each findByRole's default timeout budget on just its own
    // remaining fetch instead of the whole catalog->schema->table chain,
    // which otherwise flakes under CI's slower/loaded runners.
    await screen.findByRole("button", { name: /raw/i });
    const eventsRow = await screen.findByRole("button", { name: /events/i });
    fireEvent.contextMenu(eventsRow);
    await user.click(
      await screen.findByRole("menuitem", { name: /recount rows/i }),
    );

    await waitFor(() => expect(recounted).toBe("raw.events"));
  });

  it("renders a schema with no tables and the selection placeholder", async () => {
    server.use(
      http.get(
        "/api/workspaces/:ws/catalogs/:catalog/schemas/:schema/tables",
        () => HttpResponse.json([]),
      ),
    );
    renderWithProviders({ initialRoute: CATALOG_ROUTE });

    await screen.findByRole("button", { name: /raw/i });
    expect(screen.getByText(/to view its details/i)).toBeInTheDocument();
  });

  it("drops a non-empty schema with cascade via the schema menu", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: CATALOG_ROUTE });

    const rawRow = await screen.findByRole("button", { name: /raw/i });
    fireEvent.contextMenu(rawRow);
    await user.click(
      await screen.findByRole("menuitem", { name: /drop schema/i }),
    );

    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText(/type .* to confirm/i),
      "raw",
    );
    // First attempt hits 409 (non-empty) and reveals the cascade option.
    await user.click(
      within(dialog).getByRole("button", { name: /drop schema/i }),
    );
    const cascade =
      await within(dialog).findByLabelText(/also drop all tables/i);
    await user.click(cascade);
    await user.click(
      within(dialog).getByRole("button", { name: /drop schema/i }),
    );

    await waitFor(() => {
      expect(screen.queryByText("raw")).not.toBeInTheDocument();
    });
  });

  it("drops a table from the detail view and returns to the catalog", async () => {
    const user = userEvent.setup();
    renderWithProviders({
      initialRoute: "/acme-analytics/catalog/acme_analytics/raw/events",
    });

    await user.click(await screen.findByRole("button", { name: /^drop$/i }));
    const dialog = await screen.findByRole("dialog");
    await user.type(
      within(dialog).getByLabelText(/type .* to confirm/i),
      "events",
    );
    await user.click(
      within(dialog).getByRole("button", { name: /drop table/i }),
    );

    // Visiting the table detail page recorded it as recently viewed, so the
    // landing state we return to surfaces that entry instead of the bare
    // placeholder.
    expect(await screen.findByText("Recently viewed")).toBeInTheDocument();
    expect(screen.getByText("events")).toBeInTheDocument();
  });

  it("shows a read-access message when the row sample is denied (metadata tier)", async () => {
    // A `metadata`-tier grant lets the table load but 404s the sample.
    server.use(
      http.get(
        "/api/workspaces/:ws/catalogs/:catalog/schemas/:schema/tables/:table/sample",
        () => new HttpResponse(null, { status: 404 }),
      ),
      http.get(
        "/api/workspaces/:ws/schemas/:schema/tables/:table/sample",
        () => new HttpResponse(null, { status: 404 }),
      ),
    );
    renderWithProviders({
      initialRoute: "/acme-analytics/catalog/acme_analytics/raw/events",
    });

    expect(
      await screen.findByText(/previewing rows requires/i),
    ).toBeInTheDocument();
  });

  it("surfaces Iceberg-native metadata on the table detail view", async () => {
    renderWithProviders({
      initialRoute: "/acme-analytics/catalog/acme_analytics/raw/events",
    });

    // The events fixture carries format version, snapshot, file count, deletes.
    expect(await screen.findByText(/Iceberg v2/)).toBeInTheDocument();
    expect(screen.getByText(/128 files/)).toBeInTheDocument();
    expect(
      screen.getByText(/snapshot 7264354987654321234/),
    ).toBeInTheDocument();
    expect(screen.getByText("has deletes")).toBeInTheDocument();
  });

  it('routes "Alter table" into a worksheet seeded with ALTER SQL', async () => {
    const user = userEvent.setup();
    renderWithProviders({
      initialRoute: "/acme-analytics/catalog/acme_analytics/raw/events",
    });

    await user.click(
      await screen.findByRole("button", { name: /alter table/i }),
    );

    // Opens a worksheet named for the table, holding the ALTER statement.
    expect(
      await screen.findByRole("tab", { name: /^events(?!\.sql)/ }),
    ).toBeInTheDocument();
    expect(WORKSHEETS.at(-1)?.sql).toMatch(/^ALTER TABLE .*"events"/);
  });

  it("lists snapshot history under the History tab", async () => {
    const user = userEvent.setup();
    renderWithProviders({
      initialRoute: "/acme-analytics/catalog/acme_analytics/raw/events",
    });

    await user.click(await screen.findByRole("tab", { name: /history/i }));

    // The events fixture has a current snapshot → a non-empty, current-flagged log.
    expect(await screen.findByText("current")).toBeInTheDocument();
    expect(screen.getByText("overwrite")).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: /query at this snapshot/i }).length,
    ).toBeGreaterThan(0);
  });

  it("shows an empty state for a table with no snapshot history", async () => {
    const user = userEvent.setup();
    // The `users` fixture has snapshot_id === null → no history.
    renderWithProviders({
      initialRoute: "/acme-analytics/catalog/acme_analytics/raw/users",
    });

    await user.click(await screen.findByRole("tab", { name: /history/i }));

    expect(
      await screen.findByText(/no snapshot history yet/i),
    ).toBeInTheDocument();
  });

  it('routes "Query at this snapshot" into a worksheet seeded with time-travel SQL', async () => {
    const user = userEvent.setup();
    renderWithProviders({
      initialRoute: "/acme-analytics/catalog/acme_analytics/raw/events",
    });

    await user.click(await screen.findByRole("tab", { name: /history/i }));
    const buttons = await screen.findAllByRole("button", {
      name: /query at this snapshot/i,
    });
    await user.click(buttons[0]);

    expect(
      await screen.findByRole("tab", { name: /^events(?!\.sql)/ }),
    ).toBeInTheDocument();
    expect(WORKSHEETS.at(-1)?.sql).toMatch(/AT \(VERSION =>/);
  });
});

describe("CatalogPage tab deep-linking", () => {
  it("opens directly on the Lineage tab when linked with ?tab=lineage", async () => {
    // daily_active_users is the fixture table the lineage graph is seeded
    // for (see LineagePanel.test.tsx's TABLE_ROUTE).
    renderWithProviders({
      initialRoute:
        "/acme-analytics/catalog/acme_analytics/analytics/daily_active_users?tab=lineage",
    });

    const lineageTab = await screen.findByRole("tab", { name: /lineage/i });
    await waitFor(() =>
      expect(lineageTab).toHaveAttribute("data-state", "active"),
    );
    expect(await screen.findByTestId("lineage-graph-scroll")).toBeVisible();
  });

  it("updates the URL search param when the user switches tabs", async () => {
    const user = userEvent.setup();
    const { router } = renderWithProviders({
      initialRoute: "/acme-analytics/catalog/acme_analytics/raw/events",
    });

    await user.click(await screen.findByRole("tab", { name: /permissions/i }));

    await waitFor(() =>
      expect(router.state.location.search).toMatchObject({
        tab: "permissions",
      }),
    );
  });

  it("falls back to the Sample tab for an unrecognized ?tab= value", async () => {
    renderWithProviders({
      initialRoute:
        "/acme-analytics/catalog/acme_analytics/raw/events?tab=notreal",
    });

    const sampleTab = await screen.findByRole("tab", { name: /sample/i });
    await waitFor(() =>
      expect(sampleTab).toHaveAttribute("data-state", "active"),
    );
  });
});

describe("CatalogPage recently-viewed", () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("shows the recently-viewed list on the bare landing state", async () => {
    recordRecentlyViewed("acme-analytics", {
      type: "table",
      catalog: "acme_analytics",
      schema: "raw",
      name: "events",
    });
    renderWithProviders({ initialRoute: "/acme-analytics/catalog" });

    expect(await screen.findByText("Recently viewed")).toBeInTheDocument();
    expect(screen.getByText("events")).toBeInTheDocument();
    expect(screen.getByText("acme_analytics.raw")).toBeInTheDocument();
  });

  it("navigates to the object detail page when a recently-viewed row is clicked", async () => {
    const user = userEvent.setup();
    recordRecentlyViewed("acme-analytics", {
      type: "table",
      catalog: "acme_analytics",
      schema: "raw",
      name: "events",
    });
    const { router } = renderWithProviders({
      initialRoute: "/acme-analytics/catalog",
    });

    await user.click(await screen.findByText("events"));

    await waitFor(() =>
      expect(router.state.location.pathname).toBe(
        "/acme-analytics/catalog/acme_analytics/raw/events",
      ),
    );
  });

  it("falls back to the selection placeholder when there is no history", async () => {
    renderWithProviders({ initialRoute: "/acme-analytics/catalog" });

    expect(await screen.findByText(/to view its details/i)).toBeInTheDocument();
    expect(screen.queryByText("Recently viewed")).not.toBeInTheDocument();
  });
});

// The catalog page mirrors the worksheet's layout, so moving between the two
// does not shift the tree or change the scale of the panes.
describe("CatalogPage layout", () => {
  it("titles the page in a 36px row cell as wide as the tree, not a page header", async () => {
    renderWithProviders({ initialRoute: CATALOG_ROUTE });
    const title = await screen.findByRole("heading", { name: "Catalog" });

    expect(title.className).toContain("text-sm");
    const cell = title.parentElement!;
    expect(cell.className).toContain("w-[280px]");
    expect(cell.parentElement!.className).toContain("h-9");
  });

  it("shows no path on the landing state", async () => {
    renderWithProviders({ initialRoute: CATALOG_ROUTE });
    const title = await screen.findByRole("heading", { name: "Catalog" });

    expect(title.parentElement!.parentElement!.children).toHaveLength(1);
  });

  it("puts the selected table's path in the title row, once", async () => {
    renderWithProviders({
      initialRoute: `${CATALOG_ROUTE}/acme_analytics/raw/events`,
    });
    const title = await screen.findByRole("heading", { name: "Catalog" });
    await screen.findByRole("tab", { name: "Sample" });

    const row = title.parentElement!.parentElement!;
    expect(row).toHaveTextContent("acme-analytics");
    expect(row).toHaveTextContent("acme_analytics");
    expect(row).toHaveTextContent("raw");
    expect(row).toHaveTextContent("events");
    // Not repeated in the detail header below it.
    const header = screen
      .getByRole("button", { name: /alter table/i })
      .closest(".border-b") as HTMLElement;
    expect(within(header).queryByText("acme-analytics")).toBeNull();
  });

  it("puts a schema's path in the title row", async () => {
    renderWithProviders({
      initialRoute: `${CATALOG_ROUTE}/acme_analytics/raw`,
    });
    const title = await screen.findByRole("heading", { name: "Catalog" });
    await screen.findByText("Tables");

    const row = title.parentElement!.parentElement!;
    expect(within(row).getByText("raw").className).toContain("font-medium");
    expect(within(row).getByText("acme_analytics").className).not.toContain(
      "font-medium",
    );
  });

  it("uses the app's one tab look at the compact pane size", async () => {
    renderWithProviders({
      initialRoute: `${CATALOG_ROUTE}/acme_analytics/raw/events`,
    });
    const tab = await screen.findByRole("tab", { name: "Sample" });
    const list = tab.parentElement!;

    // The Segmented look (tabs.tsx), sized for a pane like Results | Profile.
    expect(list.className).toContain("border");
    expect(list.className).toContain("h-7");
    expect(list.className).not.toContain("bg-muted");
    expect(tab.className).toContain("data-[state=active]:bg-accent");
  });
});
