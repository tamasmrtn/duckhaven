import { describe, it, expect } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@tests/utils";
import { server } from "@tests/mock/server";
import { auditEvents } from "@/mock/fixtures/audit";

const ROUTE = "/acme-analytics/admin/audit";
const DAY = 24 * 3_600_000;

/** Every request the page makes to the audit endpoint, as its query string. */
function recordRequests(): URLSearchParams[] {
  const seen: URLSearchParams[] = [];
  server.events.on("request:start", ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname === "/api/admin/audit/events") seen.push(url.searchParams);
  });
  return seen;
}

function rowFor(label: string): HTMLElement {
  return screen.getByText(label).closest("tr")!;
}

describe("AuditPage", () => {
  it("lists events newest first, with who acted and whose access changed", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    const rows = await screen.findAllByRole("button", {
      name: /^Show details: /,
    });
    expect(rows[0]).toHaveAccessibleName(
      "Show details: Added a workspace member",
    );
    expect(rows).toHaveLength(auditEvents().length);

    const grant = within(rowFor("Granted access to data"));
    expect(grant.getByText("marton@duckhaven.local")).toBeInTheDocument();
    expect(grant.getByText("sales.analytics.orders")).toBeInTheDocument();
    expect(grant.getByText("jess@duckhaven.local")).toBeInTheDocument();
    expect(grant.getByText("catalog_grant.create")).toBeInTheDocument();
  });

  it("names the person a delegated call acted for", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    await screen.findByText("Was refused by a data grant check");
    const refused = within(rowFor("Was refused by a data grant check"));
    expect(
      refused.getByText("duckhaven-assistant@service-account.local"),
    ).toBeInTheDocument();
    expect(refused.getByText("for jess@duckhaven.local")).toBeInTheDocument();
    expect(refused.getByText("Denied")).toBeInTheDocument();
  });

  it("says who acted when no account did", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    await screen.findByText("Failed to sign in");
    expect(
      within(rowFor("Failed to sign in")).getByText("Not signed in"),
    ).toBeInTheDocument();
    expect(
      within(rowFor("Failed to sign in")).getByText("Failed"),
    ).toBeInTheDocument();
    expect(
      within(rowFor("Changed a user's global role")).getByText(
        "Identity provider",
      ),
    ).toBeInTheDocument();
  });

  it("marks a change someone made to their own access", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    await screen.findByText("Added a workspace member");
    expect(
      within(rowFor("Added a workspace member")).getByText("Own access"),
    ).toBeInTheDocument();
    expect(
      within(rowFor("Granted access to data")).queryByText("Own access"),
    ).not.toBeInTheDocument();
  });

  it("puts what happened second, with its marks beside it", async () => {
    // At 1280px the Affecting column and its "Own access" mark ran past the
    // edge, and a phone showed only When and By.
    renderWithProviders({ initialRoute: ROUTE });

    await screen.findByText("Added a workspace member");
    expect(
      screen.getAllByRole("columnheader").map((h) => h.textContent),
    ).toEqual(["When", "Action", "By", "Target", "Affecting"]);
    const action = screen.getByText("Added a workspace member").closest("td")!;
    expect(within(action).getByText("Own access")).toBeInTheDocument();
    expect(
      within(screen.getByText("Failed to sign in").closest("td")!).getByText(
        "Failed",
      ),
    ).toBeInTheDocument();
  });

  it("labels the workspace filter in words that stay once it is typed in", async () => {
    renderWithProviders({ initialRoute: ROUTE });

    const input = await screen.findByRole("textbox", { name: "workspace" });
    expect(input).toHaveAttribute("placeholder", "slug");
    expect(input.parentElement).toHaveTextContent(/^Workspace/);
  });

  it("opens a row to show what changed and where the request came from", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    const toggle = await screen.findByRole("button", {
      name: "Show details: Changed a workspace member's role",
    });
    await user.click(toggle);

    const change = screen.getByRole("region", { name: "Change" });
    expect(within(change).getByText("Role")).toBeInTheDocument();
    expect(change).toHaveTextContent("reader→writer");
    const request = screen.getByRole("region", { name: "Request" });
    expect(request).toHaveTextContent("req-ae-06");
    expect(request).toHaveTextContent("10.0.4.17");
    expect(
      screen.getByRole("button", {
        name: "Hide details: Changed a workspace member's role",
      }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("asks for the last 30 days by default", async () => {
    const seen = recordRequests();
    renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Added a workspace member");

    const since = Date.parse(seen[0].get("since")!);
    expect(Math.abs(Date.now() - 30 * DAY - since)).toBeLessThan(60_000);
    expect(seen[0].get("limit")).toBe("50");
    expect(seen[0].has("self_change")).toBe(false);
  });

  it("sends each filter in the URL to the server", async () => {
    const seen = recordRequests();
    renderWithProviders({
      initialRoute: `${ROUTE}?category=access&outcome=success&actor=u-1&subject=u-2&workspace=acme-analytics&range=all`,
    });
    await screen.findByText("Granted access to data");

    const sent = seen.at(-1)!;
    expect(Object.fromEntries(sent)).toEqual({
      category: "access",
      outcome: "success",
      actor_id: "u-1",
      subject_id: "u-2",
      workspace: "acme-analytics",
      limit: "50",
    });
    // The server's answer, not a client-side filter, decides what shows.
    expect(screen.queryByText("Failed to sign in")).not.toBeInTheDocument();
  });

  it("narrows to changes to people's own access", async () => {
    const seen = recordRequests();
    const user = userEvent.setup();
    const { router } = renderWithProviders({ initialRoute: ROUTE });
    await screen.findByText("Granted access to data");

    await user.click(
      screen.getByRole("checkbox", { name: "Changes to own access" }),
    );

    await waitFor(() =>
      expect(
        screen.queryByText("Granted access to data"),
      ).not.toBeInTheDocument(),
    );
    expect(seen.at(-1)!.get("self_change")).toBe("true");
    expect(router.state.location.search).toMatchObject({ own: true });
    expect(screen.getAllByText("Own access")).toHaveLength(2);
  });

  it("loads the next page with the cursor", async () => {
    const [first, second] = auditEvents();
    server.use(
      http.get("/api/admin/audit/events", ({ request }) => {
        const cursor = new URL(request.url).searchParams.get("cursor");
        return HttpResponse.json(
          cursor === "c-1"
            ? { items: [second], cursor: null, has_more: false }
            : { items: [first], cursor: "c-1", has_more: true },
        );
      }),
    );
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });

    expect(await screen.findByText("Showing 1 event")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText(second.label)).toBeInTheDocument();
    expect(screen.getByText("All 2 events")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Load more" }),
    ).not.toBeInTheDocument();
  });

  it("names the scope when nothing matches", async () => {
    server.use(
      http.get("/api/admin/audit/events", () =>
        HttpResponse.json({ items: [], cursor: null, has_more: false }),
      ),
    );
    renderWithProviders({ initialRoute: `${ROUTE}?category=auth` });

    expect(await screen.findByText("No events match.")).toBeInTheDocument();
    expect(
      screen.getByText("Showing sign-ins · last 30 days."),
    ).toBeInTheDocument();
  });

  it("offers a retry when the log cannot be read", async () => {
    server.use(
      http.get("/api/admin/audit/events", () =>
        HttpResponse.json(
          { error: "internal_error", message: "boom", details: null },
          { status: 500 },
        ),
      ),
    );
    renderWithProviders({ initialRoute: ROUTE });

    expect(
      await screen.findByText("Could not load the audit log."),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });
});
