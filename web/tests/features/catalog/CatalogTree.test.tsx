import { describe, it, expect, vi } from "vitest";
import { http, HttpResponse } from "msw";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  within,
  configure,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import {
  createRootRoute,
  createRouter,
  createMemoryHistory,
  RouterProvider,
} from "@tanstack/react-router";
import { CatalogTree } from "@/features/catalog/CatalogTree";
import { createTestQueryClient, createWrapper } from "@tests/utils";
import { server } from "@tests/mock/server";

// This file wraps CatalogTree in a real RouterProvider (for the hover-card's
// <Link>s), which resolves its initial route match asynchronously — under a
// full parallel test run that adds enough latency to occasionally miss
// find*'s default 1000ms window. Give it more headroom rather than chase a
// CI-load flake with no logic behind it.
configure({ asyncUtilTimeout: 3000 });

// The hover-card's "View details"/"Lineage"/"Permissions" links use
// TanStack's <Link>, which needs a real router context — a single-route
// router standing in for the app shell is enough for that, without pulling
// in the full app route tree these are otherwise isolated component tests.
// The tree starts collapsed and remembers what was opened; most tests start from
// the default catalog's `raw` schema already open, as a returning user would.
const EXPANDED_KEY = "dh-tree-expanded-acme-analytics";
const RAW_OPEN = ["c:acme_analytics", "s:acme_analytics.raw"];

function renderTree(
  onTableClick: (catalog: string, schema: string, table: string) => void,
  { expanded = RAW_OPEN }: { expanded?: string[] } = {},
) {
  if (expanded.length) localStorage.setItem(EXPANDED_KEY, JSON.stringify(expanded));
  const queryClient = createTestQueryClient();
  const rootRoute = createRootRoute({
    component: () => (
      <CatalogTree
        ws="acme-analytics"
        workspaceName="acme-analytics"
        onTableClick={onTableClick}
      />
    ),
  });
  const router = createRouter({
    routeTree: rootRoute,
    history: createMemoryHistory({ initialEntries: ["/"] }),
    defaultPendingMinMs: 0,
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
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

  it("makes the table row a drag source carrying its fully-qualified name", async () => {
    renderTree(() => {});
    const events = await screen.findByRole("button", { name: /events/i });
    expect(events).toHaveAttribute("draggable", "true");

    const setData = vi.fn();
    fireEvent.dragStart(events, {
      dataTransfer: { setData, effectAllowed: "" },
    });
    expect(setData).toHaveBeenCalledWith(
      "text/plain",
      "acme_analytics.raw.events",
    );
  });

  it("starts with every catalog collapsed", async () => {
    // Regression: the default catalog and all its schemas used to open on
    // every visit, so finding anything else meant closing them first.
    renderTree(() => {}, { expanded: [] });

    const toggle = await screen.findAllByRole("button", { name: "Expand catalog" });
    expect(toggle).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /events/i })).not.toBeInTheDocument();
  });

  it("remembers what was expanded across visits", async () => {
    const first = renderTree(() => {}, { expanded: [] });
    const [defaultCatalog] = await screen.findAllByRole("button", {
      name: "Expand catalog",
    });
    await userEvent.click(defaultCatalog);
    await screen.findByRole("button", { name: /^raw$/i });
    first.unmount();

    renderTree(() => {}, { expanded: [] });
    expect(await screen.findByRole("button", { name: /^raw$/i })).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem(EXPANDED_KEY) ?? "[]")).toContain(
      "c:acme_analytics",
    );
  });

  it("collapses everything at once", async () => {
    renderTree(() => {});
    await screen.findByRole("button", { name: /events/i });

    await userEvent.click(screen.getByRole("button", { name: "Collapse all" }));

    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /events/i })).not.toBeInTheDocument(),
    );
    expect(localStorage.getItem(EXPANDED_KEY)).toBeNull();
  });

  it("searches on the server and finds tables in catalogs nobody expanded", async () => {
    // Regression: the box used to filter only rows already loaded, so a table
    // in a collapsed catalog could not be found.
    renderTree(() => {}, { expanded: [] });
    await screen.findAllByRole("button", { name: "Expand catalog" });

    await userEvent.type(screen.getByLabelText("Search tables"), "revenue");

    const results = await screen.findByLabelText("Search results");
    // `curated.marts.revenue_daily` lives in the non-default catalog.
    expect(within(results).getByText("curated")).toBeInTheDocument();
    expect(within(results).getByText("marts")).toBeInTheDocument();
    expect(within(results).getByRole("button", { name: /revenue_daily/i })).toBeInTheDocument();
    expect(within(results).getByText("revenue", { selector: "mark" })).toBeInTheDocument();
  });

  it("returns to the remembered tree when the search is cleared", async () => {
    renderTree(() => {});
    await screen.findByRole("button", { name: /events/i });
    const box = screen.getByLabelText("Search tables");

    await userEvent.type(box, "revenue");
    await screen.findByLabelText("Search results");
    await userEvent.clear(box);

    expect(await screen.findByRole("button", { name: /events/i })).toBeInTheDocument();
    expect(screen.queryByLabelText("Search results")).not.toBeInTheDocument();
  });

  it("says when nothing matches", async () => {
    renderTree(() => {});
    await userEvent.type(await screen.findByLabelText("Search tables"), "zzz_nothing");
    expect(await screen.findByText(/No catalogs, schemas or tables match/)).toBeInTheDocument();
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

  it("renders the catalog node with a closed-book icon, not a database glyph", async () => {
    renderTree(() => {});

    const catalog = await screen.findByRole("button", {
      name: /acme_analytics/i,
    });
    expect(catalog.querySelector("svg.lucide-book")).toBeTruthy();
    expect(catalog.querySelector("svg.lucide-database")).toBeNull();
  });

  it("shows a storage-backend indicator on the catalog node", async () => {
    renderTree(() => {});

    // acme_analytics is an S3-backed catalog (fixtures) -> the Box glyph.
    const catalog = await screen.findByRole("button", {
      name: /acme_analytics/i,
    });
    expect(catalog.querySelector("svg.lucide-box")).toBeTruthy();
  });

  it("opens Catalog information from the right-click menu with backend metadata", async () => {
    renderTree(() => {});

    const catalog = await screen.findByRole("button", {
      name: /acme_analytics/i,
    });
    fireEvent.contextMenu(catalog);

    await userEvent.click(
      await screen.findByRole("menuitem", { name: /catalog information/i }),
    );

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Catalog information")).toBeInTheDocument();
    expect(within(dialog).getByText(/AWS S3/)).toBeInTheDocument();
    expect(
      within(dialog).getByText("s3://acme-data/duckhaven/"),
    ).toBeInTheDocument();
  });

  it("shows a read-only information_schema node with its supported views", async () => {
    renderTree(() => {});
    // Wait for the default catalog to expand and load.
    await screen.findByRole("button", { name: /events/i });

    const infoNode = screen.getByRole("button", {
      name: /information_schema/i,
    });
    // Read-only signalling: a lock icon and a "read-only" badge that never
    // shrinks onto a second line in a narrow sidebar.
    const badge = within(infoNode).getByText(/read-only/i);
    expect(badge).toHaveClass("shrink-0", "whitespace-nowrap");
    expect(infoNode.querySelector("svg.lucide-lock")).toBeTruthy();

    // Expanding reveals the supported views.
    await userEvent.click(infoNode);
    for (const view of ["schemata", "tables", "views"]) {
      expect(screen.getByRole("button", { name: view })).toBeInTheDocument();
    }
    // `columns` is not offered: DuckDB cannot introspect an attached Iceberg
    // relation's columns through it, so the seeded query would return an
    // UNKNOWN placeholder rather than the table's real columns.
    expect(
      screen.queryByRole("button", { name: "columns" }),
    ).not.toBeInTheDocument();
  });

  it("makes an information_schema view row a drag source", async () => {
    renderTree(() => {});
    await screen.findByRole("button", { name: /events/i });
    await userEvent.click(
      screen.getByRole("button", { name: /information_schema/i }),
    );

    const tables = screen.getByRole("button", { name: "tables" });
    expect(tables).toHaveAttribute("draggable", "true");

    const setData = vi.fn();
    fireEvent.dragStart(tables, {
      dataTransfer: { setData, effectAllowed: "" },
    });
    expect(setData).toHaveBeenCalledWith(
      "text/plain",
      "information_schema.tables",
    );
  });

  it("hides the information_schema node for a scoped catalog", async () => {
    // Under a scoped attachment the API rejects these views: DuckDB computes
    // them across every attached catalog and cannot narrow them to the
    // principal's grants, so listing tables through them would leak. Offering
    // the node would only lead the user to a 403.
    server.use(
      http.get("/api/workspaces/:ws/catalogs", () =>
        HttpResponse.json([
          {
            id: "cat-scoped",
            slug: "acme_analytics",
            name: "acme_analytics",
            kind: "iceberg_polaris" as const,
            polaris_name: "acme_analytics",
            storage_backend_kind: "s3",
            is_default: true,
            attached_workspaces: 1,
            access_mode: "scoped",
          },
        ]),
      ),
    );
    renderTree(() => {});

    await screen.findByRole("button", { name: /events/i });
    expect(
      screen.queryByRole("button", { name: /information_schema/i }),
    ).not.toBeInTheDocument();
  });

  it("seeds a scoped query when an information_schema view is clicked", async () => {
    const onMetaViewClick = vi.fn();
    localStorage.setItem(EXPANDED_KEY, JSON.stringify(RAW_OPEN));
    const { wrapper: Wrapper } = createWrapper();
    render(
      <CatalogTree
        ws="acme-analytics"
        workspaceName="acme-analytics"
        onTableClick={() => {}}
        onMetaViewClick={onMetaViewClick}
      />,
      { wrapper: Wrapper },
    );
    await screen.findByRole("button", { name: /events/i });

    await userEvent.click(
      screen.getByRole("button", { name: /information_schema/i }),
    );
    await userEvent.click(screen.getByRole("button", { name: "tables" }));
    expect(onMetaViewClick).toHaveBeenCalledWith("acme_analytics", "tables");
  });

  it("opens the new-catalog dialog from the create dropdown", async () => {
    renderTree(() => {});

    await userEvent.click(
      await screen.findByRole("button", { name: /^create$/i }),
    );
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

  it("shows a hover-preview card with table stats", async () => {
    renderTree(() => {});
    const eventsName = await screen.findByRole("button", { name: /events/i });
    const row = eventsName.closest("div") as HTMLElement;

    await userEvent.hover(row);

    // "Marton" (owner) only ever appears inside the hover card — the
    // collapsed row itself already shows a "42.1M" row-count badge, so that
    // text alone can't distinguish "the card loaded" from "the row rendered".
    expect(
      await screen.findByText("Marton", undefined, { timeout: 2000 }),
    ).toBeInTheDocument();
    expect(screen.getByText("312.0 MB")).toBeInTheDocument();
    expect(screen.getByText("acme_analytics.raw")).toBeInTheDocument();
  });

  it("links the hover card to the table's details, lineage, and permissions", async () => {
    renderTree(() => {});
    const eventsName = await screen.findByRole("button", { name: /events/i });
    const row = eventsName.closest("div") as HTMLElement;

    await userEvent.hover(row);

    const details = await screen.findByRole("link", { name: /view details/i });
    expect(details).toHaveAttribute(
      "href",
      "/acme-analytics/catalog/acme_analytics/raw/events",
    );
    const lineage = screen.getByRole("link", { name: /^lineage$/i });
    expect(lineage).toHaveAttribute(
      "href",
      "/acme-analytics/catalog/acme_analytics/raw/events?tab=lineage",
    );
    const permissions = screen.getByRole("link", { name: /^permissions$/i });
    expect(permissions).toHaveAttribute(
      "href",
      "/acme-analytics/catalog/acme_analytics/raw/events?tab=permissions",
    );
  });

  it("shares the table-detail fetch between expand and hover (no duplicate request)", async () => {
    let requests = 0;
    const onStart = ({ request }: { request: Request }) => {
      if (/\/tables\/events(\?|$)/.test(request.url)) requests++;
    };
    server.events.on("request:start", onStart);
    try {
      renderTree(() => {});

      const eventsName = await screen.findByRole("button", { name: /events/i });
      const row = eventsName.closest("div") as HTMLElement;
      fireEvent.click(
        within(row).getByRole("button", { name: /show columns/i }),
      );
      await screen.findByText("event_id");
      expect(requests).toBe(1);

      await userEvent.hover(row);
      await screen.findByText("Marton", undefined, { timeout: 2000 });
      expect(requests).toBe(1);
    } finally {
      server.events.removeListener("request:start", onStart);
    }
  });

  it("probes row-count stats via the refresh endpoint on refresh", async () => {
    let probed = false;
    server.use(
      http.post("/api/workspaces/:ws/catalogs/:catalog/refresh-stats", () => {
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

  it("probes every attached catalog, not just the workspace default", async () => {
    const probed: string[] = [];
    server.use(
      http.post(
        "/api/workspaces/:ws/catalogs/:catalog/refresh-stats",
        ({ params }) => {
          probed.push(params.catalog as string);
          return HttpResponse.json({ probed: 1 });
        },
      ),
    );
    renderTree(() => {});
    await screen.findByRole("button", { name: /events/i });

    await userEvent.click(
      screen.getByRole("button", { name: /refresh catalog/i }),
    );

    // `acme_analytics` is the default; `curated` is attached but not default.
    await waitFor(() =>
      expect([...probed].sort()).toEqual(["acme_analytics", "curated"]),
    );
  });

  it("keeps probing the other catalogs when one of them fails", async () => {
    const probed: string[] = [];
    server.use(
      http.post(
        "/api/workspaces/:ws/catalogs/:catalog/refresh-stats",
        ({ params }) => {
          const catalog = params.catalog as string;
          // One catalog failing must not abandon its siblings.
          if (catalog === "acme_analytics") {
            return HttpResponse.json(
              { detail: "No compatible agent is connected." },
              { status: 503 },
            );
          }
          probed.push(catalog);
          return HttpResponse.json({ probed: 1 });
        },
      ),
    );
    renderTree(() => {});
    await screen.findByRole("button", { name: /events/i });

    await userEvent.click(
      screen.getByRole("button", { name: /refresh catalog/i }),
    );

    await waitFor(() => expect(probed).toEqual(["curated"]));
  });
  it("shows no catalog-kind marker on any row", async () => {
    // The kind lives on the detail panel and info dialog, not the tree.
    server.use(
      http.get("/api/workspaces/:ws/catalogs", () =>
        HttpResponse.json([
          {
            id: "cat-1",
            slug: "acme_analytics",
            name: "acme-analytics",
            kind: "iceberg_polaris",
            storage_backend_kind: "s3",
            is_default: true,
            access_mode: "open",
          },
          {
            id: "cat-lake",
            slug: "lake",
            name: "Lake",
            kind: "ducklake",
            storage_backend_kind: "object_store",
            is_default: false,
            access_mode: "open",
          },
        ]),
      ),
    );
    renderTree(() => {});

    const lake = await screen.findByRole("button", { name: /^lake/i });
    const iceberg = screen.getByRole("button", { name: /^acme_analytics/i });

    // Neither the label nor the raw kind.
    for (const row of [lake, iceberg]) {
      expect(row).not.toHaveTextContent("DuckLake");
      expect(row).not.toHaveTextContent("ducklake");
      expect(row).not.toHaveTextContent("Iceberg");
      expect(row).not.toHaveTextContent("iceberg_polaris");
    }
  });
});
