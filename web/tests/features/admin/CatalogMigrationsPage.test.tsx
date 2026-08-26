import { describe, it, expect } from "vitest";
import { screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@tests/mock/server";
import { renderWithProviders } from "@tests/utils";

const ROUTE = "/acme-analytics/admin/migrations";

async function migrateButtonForAcme() {
  const cell = await screen.findByText("acme_analytics");
  const row = cell.closest("tr")!;
  return within(row).getByRole("button", { name: "Migrate…" });
}

describe("CatalogMigrationsPage", () => {
  it("lists catalogs and excludes the current backend from the target picker", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    await user.click(await migrateButtonForAcme());

    const select = await screen.findByRole("combobox", {
      name: /target backend/i,
    });
    await user.click(select);
    // cat-1's current backend is sb-1 ("acme-prod"); it must not be offered.
    expect(
      screen.queryByRole("option", { name: /acme-prod/i }),
    ).not.toBeInTheDocument();
    expect(
      await screen.findByRole("option", { name: /research/i }),
    ).toBeInTheDocument();
  });

  it("starts a migration and shows the progress panel", async () => {
    let posted: { target_storage_backend_id: string } | null = null;
    server.use(
      http.post("/api/catalogs/cat-1/migrations", async ({ request }) => {
        posted = (await request.json()) as {
          target_storage_backend_id: string;
        };
        return HttpResponse.json(
          {
            id: "mig-new",
            catalog_id: "cat-1",
            source_storage_backend_id: "sb-1",
            target_storage_backend_id: posted.target_storage_backend_id,
            status: "pending",
            tables_total: 0,
            tables_done: 0,
            bytes_total: 0,
            bytes_copied: 0,
            error: null,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            started_at: null,
            cutover_at: null,
            finished_at: null,
          },
          { status: 202 },
        );
      }),
      http.get("/api/catalogs/cat-1/migrations", () =>
        HttpResponse.json({
          items: [
            {
              id: "mig-new",
              catalog_id: "cat-1",
              source_storage_backend_id: "sb-1",
              target_storage_backend_id: "sb-2",
              status: "pending",
              tables_total: 0,
              tables_done: 0,
              bytes_total: 0,
              bytes_copied: 0,
              error: null,
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
              started_at: null,
              cutover_at: null,
              finished_at: null,
            },
          ],
          cursor: null,
          has_more: false,
        }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    await user.click(await migrateButtonForAcme());
    const select = await screen.findByRole("combobox", {
      name: /target backend/i,
    });
    await user.click(select);
    await user.click(await screen.findByRole("option", { name: /research/i }));
    await user.click(screen.getByRole("button", { name: /start migration/i }));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(
      await screen.findByText(/migrations for acme_analytics/i),
    ).toBeInTheDocument();
  });

  it("renders progress and streamed log lines for an active migration", async () => {
    server.use(
      http.get("/api/catalogs/cat-1/migrations", () =>
        HttpResponse.json({
          items: [
            {
              id: "mig-x",
              catalog_id: "cat-1",
              source_storage_backend_id: "sb-1",
              target_storage_backend_id: "sb-2",
              status: "copying",
              tables_total: 4,
              tables_done: 2,
              bytes_total: 0,
              bytes_copied: 0,
              error: null,
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
              started_at: new Date().toISOString(),
              cutover_at: null,
              finished_at: null,
            },
          ],
          cursor: null,
          has_more: false,
        }),
      ),
      http.get("/api/catalogs/cat-1/migrations/mig-x", () =>
        HttpResponse.json({
          id: "mig-x",
          catalog_id: "cat-1",
          source_storage_backend_id: "sb-1",
          target_storage_backend_id: "sb-2",
          status: "copying",
          tables_total: 4,
          tables_done: 2,
          bytes_total: 0,
          bytes_copied: 0,
          error: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          started_at: new Date().toISOString(),
          cutover_at: null,
          finished_at: null,
        }),
      ),
      http.get("/api/catalogs/cat-1/migrations/mig-x/logs", () =>
        HttpResponse.json([
          {
            seq: 1,
            level: "info",
            message: "Copying table analytics.events (2/4)",
            created_at: new Date().toISOString(),
          },
        ]),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    const cell = await screen.findByText("acme_analytics");
    const row = cell.closest("tr")!;
    await user.click(within(row).getByRole("button", { name: "Migrations" }));

    expect(await screen.findByText("2/4 tables")).toBeInTheDocument();
    expect(
      await screen.findByText(/Copying table analytics\.events/i),
    ).toBeInTheDocument();
    expect(await screen.findByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "50",
    );
  });
});
