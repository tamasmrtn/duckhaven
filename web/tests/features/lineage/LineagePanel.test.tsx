import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { server } from "@tests/mock/server";
import { renderWithProviders, screen, waitFor, within } from "@tests/utils";

const LINEAGE_URL =
  "/api/workspaces/:ws/catalogs/:catalog/schemas/:schema/tables/:table/lineage";

/** The seeded table that the fixtures give a graph to. */
const TABLE_ROUTE =
  "/acme-analytics/catalog/acme_analytics/analytics/daily_active_users";

async function openLineageTab() {
  const tab = await screen.findByRole("tab", { name: /lineage/i });
  await userEvent.click(tab);
  return tab;
}

/** Scoped to the canvas: table names also appear in the catalog tree. */
async function graph() {
  return within(await screen.findByTestId("lineage-graph-scroll"));
}

describe("LineagePanel", () => {
  it("renders the lineage tab on the table detail view", async () => {
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    expect(await screen.findByRole("tab", { name: /lineage/i })).toBeVisible();
  });

  it("shows upstream and downstream tables from the graph", async () => {
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();

    const canvas = await graph();
    expect(await canvas.findByText("events")).toBeVisible();
    expect(await canvas.findByText("funnel")).toBeVisible();
  });

  it("labels a node the viewer has no access to without naming it", async () => {
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();

    expect(await screen.findByText("Restricted")).toBeVisible();
    expect(await screen.findByText("no access")).toBeVisible();
  });

  it("shows an external source with its system name", async () => {
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();

    expect(await screen.findByText("customers")).toBeVisible();
    expect(await screen.findByText(/crm_pg/)).toBeVisible();
  });

  it("shows every relationship touching the selected node, with its providers", async () => {
    // `events` sits on two edges — one imported, one that both producers agree
    // on. Reporting only one of them would misstate where the data came from.
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();

    const canvas = await graph();
    await userEvent.click(await canvas.findByText("events"));

    expect(await screen.findAllByText("dbt")).toHaveLength(2);
    expect(await screen.findByText("execution")).toBeVisible();
    expect(await screen.findByText(/Created from/)).toBeVisible();
    expect(await screen.findByText(/Declared dependency/)).toBeVisible();
  });

  it("refetches when the depth changes", async () => {
    const depths: string[] = [];
    server.use(
      http.get(LINEAGE_URL, ({ request }) => {
        depths.push(new URL(request.url).searchParams.get("depth") ?? "");
        return HttpResponse.json({
          root: "r",
          nodes: [],
          edges: [],
          truncated: false,
        });
      }),
    );
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();
    await waitFor(() => expect(depths).toContain("2"));

    await userEvent.click(screen.getByRole("button", { name: "3" }));

    await waitFor(() => expect(depths).toContain("3"));
  });

  it("shows the empty state for a table with no lineage", async () => {
    renderWithProviders({
      initialRoute: "/acme-analytics/catalog/acme_analytics/analytics/funnel",
    });
    await openLineageTab();

    expect(
      await screen.findByText(/No lineage recorded for this table yet/i),
    ).toBeVisible();
  });

  it("warns when the graph was truncated", async () => {
    server.use(
      http.get(LINEAGE_URL, () =>
        HttpResponse.json({
          root: "r",
          nodes: [
            {
              key: "r",
              kind: "table",
              catalog: "acme_analytics",
              schema_name: "analytics",
              table: "daily_active_users",
              system: null,
              distance: 0,
            },
            {
              key: "u",
              kind: "table",
              catalog: "acme_analytics",
              schema_name: "raw",
              table: "events",
              system: null,
              distance: -1,
            },
          ],
          edges: [
            {
              source_key: "u",
              target_key: "r",
              operation: "insert",
              providers: ["execution"],
              confidence: "exact",
              first_seen_at: "2026-01-01T00:00:00Z",
              last_seen_at: "2026-01-01T00:00:00Z",
              observation_count: 1,
              last_query_id: null,
              columns: [],
            },
          ],
          truncated: true,
        }),
      ),
    );
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();

    expect(await screen.findByText(/larger than we render/i)).toBeVisible();
  });

  it("surfaces a failed request instead of rendering an empty graph", async () => {
    server.use(
      http.get(LINEAGE_URL, () =>
        HttpResponse.json({ detail: "nope" }, { status: 500 }),
      ),
    );
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();

    expect(await screen.findByText(/Could not load lineage/i)).toBeVisible();
  });
});
