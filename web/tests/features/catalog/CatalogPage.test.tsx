import { describe, it, expect } from "vitest";
import { screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@tests/mock/server";
import { renderWithProviders } from "@tests/utils";

const CATALOG_ROUTE = "/acme-analytics/catalog";

describe("CatalogPage", () => {
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

    // The tree auto-expands schemas, so the new table appears on refetch.
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
      http.get("/api/workspaces/:ws/schemas/:schema/tables", () =>
        HttpResponse.json([]),
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

    expect(await screen.findByText(/to view its details/i)).toBeInTheDocument();
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

    // Navigates to the worksheet, seeding a new tab from the catalog action.
    expect(await screen.findByText(/from catalog/i)).toBeInTheDocument();
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

    expect(await screen.findByText(/from catalog/i)).toBeInTheDocument();
  });
});
