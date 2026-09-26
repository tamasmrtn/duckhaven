import { describe, it, expect } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "@tests/utils";
import { server } from "@tests/mock/server";

const SCHEMA_DETAIL = "/acme-analytics/catalog/acme_analytics/raw";

describe("SchemaDetail", () => {
  it("shows the overview with a table count and the three tabs", async () => {
    renderWithProviders({ initialRoute: SCHEMA_DETAIL });

    expect(await screen.findByText("Tables")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /overview/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /details/i })).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /permissions/i }),
    ).toBeInTheDocument();
  });
});

describe("SchemaDetail stats", () => {
  // Regression: the stats sat in three fixed columns of a 512px grid, so a
  // row count in the tens of millions ran out of its card into the next one.
  it("sizes each stat card to its value instead of a fixed column", async () => {
    server.use(
      http.get(
        "/api/workspaces/:ws/catalogs/:catalog/schemas/:schema/tables",
        () =>
          HttpResponse.json([
            { name: "lineitem", row_count: 59_986_052, size_bytes: 2e9 },
            { name: "orders", row_count: 26_600_030, size_bytes: 1e9 },
          ]),
      ),
    );
    renderWithProviders({ initialRoute: SCHEMA_DETAIL });

    const value = await screen.findByText((86_586_082).toLocaleString());
    const card = value.parentElement!;
    const row = card.parentElement!;

    expect(card.className).toContain("min-w-[10rem]");
    expect(row.className).toContain("flex-wrap");
    expect(row.className).not.toMatch(/grid-cols-|max-w-/);
  });
});

describe("SchemaDetail sizes", () => {
  const tablesRoute =
    "/api/workspaces/:ws/catalogs/:catalog/schemas/:schema/tables";

  function stat(label: RegExp) {
    return screen.getByText(label).parentElement!;
  }

  // Regression: an Iceberg listing carries no sizes, and the overview added
  // those unknowns up as 0, so 86M rows read "0 KB".
  it("says the size is unknown when no table's size is", async () => {
    server.use(
      http.get(tablesRoute, () =>
        HttpResponse.json([
          { name: "lineitem", format: "ICEBERG", row_count: 59_986_052, size_bytes: null },
          { name: "orders", format: "ICEBERG", row_count: 26_600_030, size_bytes: null },
        ]),
      ),
    );
    renderWithProviders({ initialRoute: SCHEMA_DETAIL });

    await screen.findByText((86_586_082).toLocaleString());
    expect(stat(/^Size$/)).toHaveTextContent("—");
  });

  it("says how many tables a partial total covers", async () => {
    server.use(
      http.get(tablesRoute, () =>
        HttpResponse.json([
          { name: "lineitem", format: "ICEBERG", row_count: 10, size_bytes: 2_223_533_507 },
          { name: "orders", format: "ICEBERG", row_count: 10, size_bytes: null },
        ]),
      ),
    );
    renderWithProviders({ initialRoute: SCHEMA_DETAIL });

    expect(await screen.findByText("Size · 1 of 2 tables")).toBeInTheDocument();
    expect(stat(/^Size · 1 of 2 tables$/)).toHaveTextContent("2.1 GB");
  });

  // Regression: a DuckLake table whose rows are inlined in the catalog has no
  // data files, and read "0 KB" beside its row count.
  it("labels an inlined DuckLake table instead of calling it 0 bytes", async () => {
    server.use(
      http.get(tablesRoute, () =>
        HttpResponse.json([
          { name: "region", format: "DUCKLAKE", row_count: 5, size_bytes: 0, data_file_count: 0 },
          { name: "nation", format: "DUCKLAKE", row_count: 25, size_bytes: 2554, data_file_count: 1 },
        ]),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: SCHEMA_DETAIL });

    await user.click(await screen.findByRole("tab", { name: /details/i }));

    const region = (await screen.findByText("region")).closest("tr")!;
    const inlined = within(region).getByText("Inlined");
    expect(inlined).toHaveAttribute("title", expect.stringMatching(/catalog database/));
    const nation = screen.getByText("nation").closest("tr")!;
    expect(nation).toHaveTextContent("2.5 KB");
  });
});
