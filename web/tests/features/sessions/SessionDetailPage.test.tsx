import { describe, it, expect } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@tests/utils";

const LIVE = "/acme-analytics/sessions/sess-live";

describe("SessionDetailPage", () => {
  it("shows the statement timeline in execution order, not newest first", async () => {
    // A session is one workload read top to bottom — the whole point of grouping
    // a dbt run's statements instead of leaving them as N orphan history rows.
    renderWithProviders({ initialRoute: LIVE });

    const table = await screen.findByRole("table");
    const sql = within(table)
      .getAllByRole("row")
      .slice(1)
      .map((row) => row.querySelector("pre")?.textContent ?? "");

    expect(sql).toEqual([
      "CREATE OR REPLACE TABLE analytics.stg_orders AS SELECT * FROM raw.orders",
      "CREATE OR REPLACE TABLE analytics.fct_orders AS SELECT * FROM analytics.stg_orders",
      "SELECT count(*) FROM analytics.stg_orders",
    ]);
  });

  it("shows per-statement status and the failing statement's error", async () => {
    renderWithProviders({ initialRoute: LIVE });
    await screen.findByRole("table");

    expect(screen.getByRole("status", { name: "failed" })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "running" })).toBeInTheDocument();
    expect(
      screen.getByText(/does not exist/),
    ).toBeInTheDocument();
  });

  it("attributes the session to its principal, client, agent and catalog", async () => {
    renderWithProviders({ initialRoute: LIVE });
    await screen.findByRole("table");

    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("dbt-duckhaven 1.2.0")).toBeInTheDocument();
    expect(screen.getByText("warehouse-a")).toBeInTheDocument();
    expect(screen.getByText("analytics")).toBeInTheDocument();
  });

  it("explains why a reaped session ended", async () => {
    renderWithProviders({
      initialRoute: "/acme-analytics/sessions/sess-expired",
    });

    expect(await screen.findByText("reaped — idle")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "expired" })).toBeInTheDocument();
  });

  it("navigates from a statement into the existing query profile view", async () => {
    const user = userEvent.setup();
    const { router } = renderWithProviders({ initialRoute: LIVE });
    const table = await screen.findByRole("table");

    await user.click(within(table).getAllByRole("row")[1]);

    expect(router.state.location.pathname).toBe(
      "/acme-analytics/queries/sessq-1",
    );
  });

  it("says so when a session ran no statements", async () => {
    renderWithProviders({
      initialRoute: "/acme-analytics/sessions/sess-failed",
    });

    expect(
      await screen.findByText("This connection has not run any statements."),
    ).toBeInTheDocument();
  });
});
