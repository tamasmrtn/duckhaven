import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { server } from "@tests/mock/server";
import { makeHiddenLineage, makeLineage } from "@/mock/fixtures/lineage";
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

    // Both dbt claims have gone stale in the fixture, so the chips read
    // "dbt · stale"; the execution one still confirms its edge.
    expect(await screen.findAllByText(/^dbt/)).toHaveLength(2);
    expect(await screen.findByText("execution")).toBeVisible();
    // Incoming: `events` was declared as depending on the external source.
    expect(await screen.findByText(/Declared dependency on/)).toBeVisible();
    // Outgoing: `events` was used to build the root table.
    expect(await screen.findByText(/Used to create/)).toBeVisible();
  });

  it("describes an outgoing edge from the selected node's side", async () => {
    // Regression: every edge used the incoming phrasing, so an outgoing one read
    // as "Created from <the table it actually created> (downstream)" — stating
    // the relationship backwards on the one screen whose job is direction.
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();

    const canvas = await graph();
    await userEvent.click(await canvas.findByText("events"));

    // The label and the table name are separate elements, so assert on the row.
    const label = await screen.findByText(/Used to create/);
    expect(label.textContent).toMatch(/daily_active_users/);
    expect(screen.queryByText(/\(downstream\)/)).toBeNull();
    expect(screen.queryByText(/Created from/)).toBeNull();
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
          hidden: false,
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

  it("does not claim there is no lineage when only one direction is empty", async () => {
    // A table can have upstream lineage and no downstream. Saying "no lineage
    // recorded" while Downstream is selected gives the user no reason to try
    // the other toggle.
    server.use(
      http.get(LINEAGE_URL, ({ request }) => {
        const direction = new URL(request.url).searchParams.get("direction");
        if (direction === "downstream") {
          return HttpResponse.json({
            root: "r",
            nodes: [],
            edges: [],
            truncated: false,
            hidden: false,
          });
        }
        return HttpResponse.json(makeLineage());
      }),
    );
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();
    await userEvent.click(screen.getByRole("button", { name: "Downstream" }));

    expect(await screen.findByText(/No downstream lineage/i)).toBeVisible();
    expect(screen.queryByText(/No lineage recorded/i)).toBeNull();
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
              providers: [
                {
                  name: "execution",
                  first_seen_at: "2026-01-01T00:00:00Z",
                  last_seen_at: "2026-01-01T00:00:00Z",
                  observation_count: 1,
                  stale: false,
                },
              ],
              confidence: "exact",
              first_seen_at: "2026-01-01T00:00:00Z",
              last_seen_at: "2026-01-01T00:00:00Z",
              observation_count: 1,
              stale: false,
              last_query_id: null,
              columns: [],
            },
          ],
          truncated: true,
          hidden: false,
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

  it("says the graph is incomplete rather than showing it as whole", async () => {
    server.use(
      http.get(LINEAGE_URL, () =>
        HttpResponse.json({ ...makeLineage(), hidden: true }),
      ),
    );
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();

    expect(
      await screen.findByText(/outside this workspace and is not shown/i),
    ).toBeVisible();
  });

  it("does not claim there is no lineage when all of it is out of reach", async () => {
    // The wrong answer this signal exists to prevent: telling someone nothing
    // depends on a table when something does and they simply cannot see it.
    server.use(
      http.get(LINEAGE_URL, () =>
        HttpResponse.json(
          makeHiddenLineage("cat:0/analytics/daily_active_users"),
        ),
      ),
    );
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();

    expect(
      await screen.findByText(/lineage is outside this workspace/i),
    ).toBeVisible();
    expect(screen.queryByText(/No lineage recorded/i)).toBeNull();
  });

  it("names nothing about the part of the graph it withheld", async () => {
    server.use(
      http.get(LINEAGE_URL, () =>
        HttpResponse.json(
          makeHiddenLineage("cat:0/analytics/daily_active_users"),
        ),
      ),
    );
    const { container } = renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();
    await screen.findByText(/lineage is outside this workspace/i);

    // A count would say how much is out there and a placeholder would say
    // where; the panel is entitled to neither.
    expect(container.textContent).not.toMatch(/\d+ (table|node|relationship)/i);
  });

  it("marks a producer that has stopped confirming its edge", async () => {
    // dbt went quiet months ago while execution still confirms the same pair.
    // The stale one has to be visible as such without condemning the edge.
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();

    const canvas = await graph();
    await userEvent.click(await canvas.findByText("events"));

    expect((await screen.findAllByText(/dbt · stale/)).length).toBeGreaterThan(
      0,
    );
    expect(screen.queryByText("execution · stale")).toBeNull();
  });
});

describe("LineagePanel column detail", () => {
  it("asks for no column detail until a node is opened", async () => {
    // The default has to stay the table graph. Column detail scales with how
    // wide the tables are, so requesting it unasked would make opening the tab
    // cost something that depends on tables nobody is looking at.
    const requests: string[] = [];
    server.use(
      http.get(LINEAGE_URL, ({ request }) => {
        requests.push(new URL(request.url).search);
        return HttpResponse.json(makeLineage());
      }),
    );
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();
    await (await graph()).findByText("events");

    expect(requests.length).toBeGreaterThan(0);
    expect(requests.every((q) => !q.includes("columns_for"))).toBe(true);
  });

  it("offers no strip on a node with nothing to open", async () => {
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();
    const canvas = await graph();
    await canvas.findByText("events");

    // `customers` is external: its columns could not be tied to an asset, so
    // its count is zero and there is nothing to offer opening.
    expect(
      canvas.queryByRole("button", { name: /columns for customers/i }),
    ).toBeNull();
  });

  it("names how many columns are inside before anything is opened", async () => {
    // The whole point of the strip over a bare icon: it answers "is there
    // anything in here" without a click.
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();
    const canvas = await graph();

    expect(await canvas.findByText("4 columns")).toBeVisible();
    expect(await canvas.findByText("3 columns")).toBeVisible();
  });

  it("requests and shows a node's columns when it is expanded", async () => {
    const requests: string[] = [];
    server.use(
      http.get(LINEAGE_URL, ({ request }) => {
        const url = new URL(request.url);
        requests.push(url.search);
        const graph = makeLineage();
        const wanted = new Set(url.searchParams.getAll("columns_for"));
        return HttpResponse.json({
          ...graph,
          edges: graph.edges.map((e) =>
            wanted.has(e.source_key) || wanted.has(e.target_key)
              ? e
              : { ...e, columns: [] },
          ),
        });
      }),
    );
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();
    const canvas = await graph();

    await userEvent.click(
      await canvas.findByRole("button", {
        name: /show \d+ columns for events/i,
      }),
    );

    await waitFor(() =>
      expect(requests.some((q) => q.includes("columns_for"))).toBe(true),
    );
    expect(await canvas.findByText("occurred_at")).toBeVisible();
    expect(await canvas.findByText("session_id")).toBeVisible();
  });

  it("collapses again, hiding the columns", async () => {
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();
    const canvas = await graph();

    const toggle = await canvas.findByRole("button", {
      name: /show \d+ columns for events/i,
    });
    await userEvent.click(toggle);
    expect(await canvas.findByText("occurred_at")).toBeVisible();

    await userEvent.click(
      await canvas.findByRole("button", {
        name: /hide \d+ columns for events/i,
      }),
    );
    await waitFor(() => expect(canvas.queryByText("occurred_at")).toBeNull());
  });

  it("draws column links once both ends of an edge are open", async () => {
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();
    const canvas = await graph();

    await userEvent.click(
      await canvas.findByRole("button", {
        name: /show \d+ columns for events/i,
      }),
    );
    expect(canvas.queryAllByTestId("lineage-column-link")).toHaveLength(0);

    await userEvent.click(
      await canvas.findByRole("button", {
        name: /show \d+ columns for daily_active_users/i,
      }),
    );
    await waitFor(() =>
      expect(
        canvas.queryAllByTestId("lineage-column-link").length,
      ).toBeGreaterThan(0),
    );
  });

  it("says a relationship carries no columns rather than showing nothing", async () => {
    // The finding the table graph could never state. It must not read the same
    // as "we could not work it out".
    //
    // The root is opened rather than `events`, because the verdict belongs to
    // the daily_active_users -> funnel edge, and only opening a node on that
    // edge causes its detail to be fetched.
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();
    const canvas = await graph();

    await userEvent.click(
      await canvas.findByRole("button", {
        name: /show \d+ columns for daily_active_users/i,
      }),
    );
    await userEvent.click(await canvas.findByText("funnel"));

    expect(
      await screen.findByText(/no columns flow along this/i),
    ).toBeVisible();
  });

  it("says column detail is unavailable when nothing worked it out", async () => {
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();
    const canvas = await graph();

    await userEvent.click(
      await canvas.findByRole("button", {
        name: /show \d+ columns for events/i,
      }),
    );
    await userEvent.click(await canvas.findByText("customers"));

    expect(
      await screen.findByText(/column detail is not available/i),
    ).toBeVisible();
  });

  it("warns when column detail was capped", async () => {
    server.use(
      http.get(LINEAGE_URL, () =>
        HttpResponse.json({ ...makeLineage(), columns_truncated: true }),
      ),
    );
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();

    expect(
      await screen.findByText(/more column detail here than we show/i),
    ).toBeVisible();
  });

  it("leaves the table-level graph intact when nothing has column detail", async () => {
    server.use(
      http.get(LINEAGE_URL, () => {
        const graph = makeLineage();
        // A server that derived nothing reports it on both sides: no mappings
        // on the edges, and no counts on the nodes.
        return HttpResponse.json({
          ...graph,
          nodes: graph.nodes.map((n) => ({ ...n, column_count: 0 })),
          edges: graph.edges.map((e) => ({
            ...e,
            columns: [],
            column_lineage: "unknown" as const,
          })),
        });
      }),
    );
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();
    const canvas = await graph();

    expect(await canvas.findByText("events")).toBeVisible();
    expect(
      canvas.queryAllByRole("button", { name: /show \d+ column/i }),
    ).toEqual([]);
  });
});

describe("LineagePanel column detail scoping", () => {
  it("says nothing about columns for an edge whose detail was never fetched", async () => {
    // Opening one node must not make the panel pronounce on a different edge.
    // Detail arrives only for the nodes named in `columns_for`, so an
    // unrequested edge comes back with an empty `columns` whatever its state —
    // and reading that as "no columns flow" reports a finding about the data
    // that actually came from not having asked.
    renderWithProviders({ initialRoute: TABLE_ROUTE });
    await openLineageTab();
    const canvas = await graph();

    await userEvent.click(
      await canvas.findByRole("button", {
        name: /show \d+ columns for events/i,
      }),
    );
    await canvas.findByText("occurred_at");

    // `funnel` sits downstream; nothing about its edge was requested.
    await userEvent.click(await canvas.findByText("funnel"));

    expect(screen.queryByText(/no columns flow along this/i)).toBeNull();
    expect(screen.queryByText(/column detail is not available/i)).toBeNull();
  });
});
