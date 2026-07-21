import { describe, it, expect } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@tests/utils";
import { server } from "@tests/mock/server";
import { CURRENT_USER } from "@/mock/fixtures/users";

const ROUTE = "/acme-analytics/sessions";

describe("SessionsPage", () => {
  it("shows only sessions still holding an agent slot on the Live tab", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    // sess-live is open; the closed / expired / failed fixtures are terminal.
    expect(await screen.findByRole("status", { name: "open" })).toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "closed" })).not.toBeInTheDocument();
    expect(screen.queryByRole("status", { name: "expired" })).not.toBeInTheDocument();
  });

  it("renders the live sessions as a semantic table with the client that opened them", async () => {
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByRole("status", { name: "open" });

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "Client" }),
    ).toBeInTheDocument();
    // Captured from the connector's User-Agent, not supplied by the client body.
    expect(screen.getByText("dbt-duckhaven 1.2.0")).toBeInTheDocument();
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("warehouse-a")).toBeInTheDocument();
  });

  it("renders close reasons as prose, never the raw enum", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByRole("status", { name: "open" });

    await user.click(screen.getByRole("tab", { name: "All" }));

    expect(await screen.findByText("closed by client")).toBeInTheDocument();
    expect(screen.getByText("reaped — idle")).toBeInTheDocument();
    expect(screen.getByText("agent disconnected")).toBeInTheDocument();
    expect(screen.queryByText("max_lifetime")).not.toBeInTheDocument();
    expect(screen.queryByText("agent_disconnect")).not.toBeInTheDocument();
  });

  it("lets an admin force-close a live session", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByRole("status", { name: "open" });

    await user.click(screen.getByRole("button", { name: "Force close" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText("Force close this session?"),
    ).toBeInTheDocument();
    await user.click(
      within(dialog).getByRole("button", { name: "Force close" }),
    );

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    // The handler flips the fixture terminal, so it leaves the Live tab.
    await waitFor(() => {
      expect(
        screen.queryByRole("status", { name: "open" }),
      ).not.toBeInTheDocument();
    });
  });

  it("hides force-close from a member without queries:admin", async () => {
    server.use(
      http.get("/api/me", () =>
        HttpResponse.json({ ...CURRENT_USER, role: "user", permissions: [] }),
      ),
    );
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByRole("status", { name: "open" });

    expect(
      screen.queryByRole("button", { name: "Force close" }),
    ).not.toBeInTheDocument();
  });

  it("says sessions are not enabled when the endpoint 404s", async () => {
    // The whole surface is gated on SQL_SESSIONS_ENABLED; a generic failure would
    // read as a bug rather than a deliberately-off feature.
    server.use(
      http.get("/api/workspaces/:ws/sql/sessions", () =>
        HttpResponse.json({ detail: "SQL sessions are not enabled" }, { status: 404 }),
      ),
    );
    renderWithProviders({ initialRoute: ROUTE });

    expect(
      await screen.findByText("SQL sessions are not enabled."),
    ).toBeInTheDocument();
  });

  it("shows an empty state when nothing is pinning capacity", async () => {
    server.use(
      http.get("/api/workspaces/:ws/sql/sessions", () => HttpResponse.json([])),
    );
    renderWithProviders({ initialRoute: ROUTE });

    expect(await screen.findByText("No live sessions.")).toBeInTheDocument();
  });
});
