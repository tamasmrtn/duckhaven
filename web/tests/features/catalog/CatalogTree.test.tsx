import { describe, it, expect, vi } from "vitest";
import { http, HttpResponse } from "msw";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CatalogTree } from "@/features/catalog/CatalogTree";
import { createWrapper } from "@tests/utils";
import { server } from "@tests/mock/server";

function renderTree(
  onTableClick: (catalog: string, schema: string, table: string) => void,
) {
  const { wrapper: Wrapper } = createWrapper();
  return render(
    <CatalogTree
      ws="acme-analytics"
      workspaceName="acme-analytics"
      onTableClick={onTableClick}
    />,
    { wrapper: Wrapper },
  );
}

describe("CatalogTree", () => {
  it("reports the catalog, schema and table when a table row is clicked", async () => {
    const onTableClick = vi.fn();
    renderTree(onTableClick);

    const events = await screen.findByRole("button", { name: /events/i });
    fireEvent.click(events);
    expect(onTableClick).toHaveBeenCalledWith(
      "acme_analytics",
      "raw",
      "events",
    );
  });

  it("filters table rows by the search box", async () => {
    renderTree(() => {});

    // Schemas auto-expand, so sibling tables are visible up front.
    await screen.findByRole("button", { name: /events/i });
    expect(
      screen.getByRole("button", { name: /page_views/i }),
    ).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Search tables"), "events");

    await waitFor(() => {
      expect(
        screen.queryByRole("button", { name: /page_views/i }),
      ).not.toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /events/i })).toBeInTheDocument();
  });

  it("expands a table to reveal its columns", async () => {
    renderTree(() => {});

    const eventsName = await screen.findByRole("button", { name: /events/i });
    // Columns are not shown until the row is expanded.
    expect(screen.queryByText("event_id")).not.toBeInTheDocument();

    // The expand toggle sits alongside the table name in the same row.
    const row = eventsName.closest("div") as HTMLElement;
    fireEvent.click(within(row).getByRole("button", { name: /show columns/i }));

    expect(await screen.findByText("event_id")).toBeInTheDocument();
    expect(screen.getByText("event_type")).toBeInTheDocument();
  });

  it("opens the new-catalog dialog from the create dropdown", async () => {
    renderTree(() => {});

    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));
    await userEvent.click(
      await screen.findByRole("menuitem", { name: /create catalog/i }),
    );

    expect(await screen.findByText("New catalog")).toBeInTheDocument();
  });

  it("creates a schema in a chosen catalog from the create dropdown", async () => {
    renderTree(() => {});
    // Wait for the tree (and its catalogs) to load.
    await screen.findByRole("button", { name: /events/i });

    await userEvent.click(screen.getByRole("button", { name: /^create$/i }));
    await userEvent.click(
      await screen.findByRole("menuitem", { name: /create schema/i }),
    );

    // The dialog offers a catalog picker; it defaults to the default catalog.
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("New schema")).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/catalog/i)).toBeInTheDocument();

    await userEvent.type(within(dialog).getByLabelText(/^name$/i), "gold");
    await userEvent.click(
      within(dialog).getByRole("button", { name: /^create$/i }),
    );

    // The new schema appears under the (default) catalog node.
    expect(await screen.findByText("gold")).toBeInTheDocument();
  });

  it("refetches the catalog when the refresh button is clicked", async () => {
    renderTree(() => {});

    // Wait for the initial catalog load.
    await screen.findByRole("button", { name: /events/i });

    // The catalog now reports a schema created out-of-band (e.g. from the
    // worksheet); the refresh button must surface it.
    server.use(
      http.get("/api/workspaces/:ws/catalogs/:catalog/schemas", () =>
        HttpResponse.json([
          {
            name: "fresh_schema",
            catalog: "acme_analytics",
            workspace_id: "x",
          },
        ]),
      ),
    );

    await userEvent.click(
      screen.getByRole("button", { name: /refresh catalog/i }),
    );

    expect(
      await screen.findByRole("button", { name: /fresh_schema/i }),
    ).toBeInTheDocument();
  });

  it("renders the system catalog read-only without detach/drop actions", async () => {
    // The system catalog is built in, attached to every workspace.
    server.use(
      http.get("/api/workspaces/:ws/catalogs", () =>
        HttpResponse.json([
          {
            id: "cat-system",
            slug: "duckhaven",
            name: "System",
            polaris_name: "duckhaven",
            storage_backend_id: "sb-4",
            storage_backend_kind: "object_store",
            created_at: "2026-01-01T00:00:00Z",
            is_default: false,
            attached_workspaces: 4,
            is_system: true,
          },
        ]),
      ),
    );
    renderTree(() => {});

    const node = await screen.findByRole("button", { name: /duckhaven/i });
    expect(screen.getByText(/system · read-only/i)).toBeInTheDocument();

    // The context menu offers only the built-in read-only notice — no
    // create-schema, detach, or drop.
    fireEvent.contextMenu(node);
    expect(
      await screen.findByText(/built-in read-only catalog/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: /detach from workspace/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("menuitem", { name: /drop catalog/i }),
    ).not.toBeInTheDocument();
  });

  it("probes row-count stats via the refresh endpoint on refresh", async () => {
    let probed = false;
    server.use(
      http.post("/api/workspaces/:ws/schemas/refresh-stats", () => {
        probed = true;
        return HttpResponse.json({ probed: 1 });
      }),
    );
    renderTree(() => {});
    await screen.findByRole("button", { name: /events/i });

    await userEvent.click(
      screen.getByRole("button", { name: /refresh catalog/i }),
    );

    await waitFor(() => expect(probed).toBe(true));
  });
});
