import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
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
