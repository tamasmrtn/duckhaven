import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@tests/mock/server";
import { renderWithProviders } from "@tests/utils";

// ag-5 (warehouse-a) is a running elastic agent the mock user administers.
const ELASTIC = "/acme-analytics/compute/ag-5";

/** Serve one agent at a chosen tier, leaving every other handler intact. */
function agentAtTier(tier: string | null, mode = "open") {
  return http.get("/api/admin/agents/ag-5", () =>
    HttpResponse.json({
      id: "ag-5",
      name: "warehouse-a",
      status: "healthy",
      capabilities: null,
      last_ping_at: null,
      created_at: "2026-06-01T00:00:00Z",
      provider: "azure_aci",
      lifecycle: "running",
      requested_cpu: 4,
      requested_memory_gb: 16,
      hourly_cost: 0.28,
      access_tier: tier,
      access_mode: mode,
    }),
  );
}

describe("AgentAccessTab", () => {
  describe("tab visibility", () => {
    it("offers the Access tab to an admin-tier holder", async () => {
      renderWithProviders({ initialRoute: ELASTIC });
      expect(
        await screen.findByRole("tab", { name: /access/i }),
      ).toBeInTheDocument();
    });

    it.each(["use", "operate"])(
      "hides the Access tab from a %s-tier holder",
      async (tier) => {
        server.use(agentAtTier(tier));
        renderWithProviders({ initialRoute: ELASTIC });

        // Wait for the page to settle on a tab that always renders.
        expect(
          await screen.findByRole("tab", { name: /overview/i }),
        ).toBeInTheDocument();
        expect(
          screen.queryByRole("tab", { name: /access/i }),
        ).not.toBeInTheDocument();
      },
    );
  });

  describe("access mode", () => {
    it("switches the agent to restricted", async () => {
      let mode: string | null = null;
      server.use(
        http.patch(
          "/api/admin/agents/ag-5/access-mode",
          async ({ request }) => {
            const body = (await request.json()) as { access_mode: string };
            mode = body.access_mode;
            return HttpResponse.json({
              agent_id: "ag-5",
              access_mode: body.access_mode,
              grants: [],
              principals: [],
            });
          },
        ),
      );
      const user = userEvent.setup();
      renderWithProviders({ initialRoute: ELASTIC });
      await user.click(await screen.findByRole("tab", { name: /access/i }));

      await user.click(
        await screen.findByRole("combobox", { name: /access mode/i }),
      );
      await user.click(
        await screen.findByRole("option", { name: /restricted/i }),
      );

      await waitFor(() => expect(mode).toBe("restricted"));
    });

    it("explains that an open agent is usable by everyone", async () => {
      const user = userEvent.setup();
      renderWithProviders({ initialRoute: ELASTIC });
      await user.click(await screen.findByRole("tab", { name: /access/i }));

      expect(
        await screen.findByText(/anyone signed in can run work/i),
      ).toBeInTheDocument();
    });
  });

  describe("granting", () => {
    it("grants a tier to a user", async () => {
      let sent: unknown = null;
      server.use(
        http.put("/api/admin/agents/ag-5/grants", async ({ request }) => {
          sent = await request.json();
          return HttpResponse.json(
            {
              id: "g-1",
              user_id: "u-1",
              user_name: "Ada Lovelace",
              workspace_id: null,
              workspace_name: null,
              tier: "operate",
              created_at: "2026-07-29T00:00:00Z",
            },
            { status: 201 },
          );
        }),
      );
      const user = userEvent.setup();
      renderWithProviders({ initialRoute: ELASTIC });
      await user.click(await screen.findByRole("tab", { name: /access/i }));

      await user.click(
        await screen.findByRole("combobox", { name: /grant to/i }),
      );
      await user.click(
        await screen.findByRole("option", { name: /ada lovelace/i }),
      );
      await user.click(
        await screen.findByRole("combobox", { name: /^tier$/i }),
      );
      await user.click(await screen.findByRole("option", { name: /operate/i }));
      await user.click(screen.getByRole("button", { name: /^grant$/i }));

      await waitFor(() =>
        expect(sent).toEqual({ user_id: "u-1", tier: "operate" }),
      );
    });

    it("never offers the admin tier for a workspace grantee", async () => {
      const user = userEvent.setup();
      renderWithProviders({ initialRoute: ELASTIC });
      await user.click(await screen.findByRole("tab", { name: /access/i }));

      await user.click(
        await screen.findByRole("combobox", { name: /grant to/i }),
      );
      await user.click(
        await screen.findByRole("option", { name: /acme analytics/i }),
      );
      await user.click(
        await screen.findByRole("combobox", { name: /^tier$/i }),
      );

      // Delegating grant/revoke to "whoever is in workspace W" would make the
      // ACL unauditable, so Tier 3 is user-only.
      expect(
        await screen.findByRole("option", { name: /^use/i }),
      ).toBeInTheDocument();
      expect(
        screen.getByRole("option", { name: /^operate/i }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("option", { name: /^admin/i }),
      ).not.toBeInTheDocument();
    });

    it("sends workspace_id for a workspace grantee", async () => {
      let sent: unknown = null;
      server.use(
        http.put("/api/admin/agents/ag-5/grants", async ({ request }) => {
          sent = await request.json();
          return HttpResponse.json({}, { status: 201 });
        }),
      );
      const user = userEvent.setup();
      renderWithProviders({ initialRoute: ELASTIC });
      await user.click(await screen.findByRole("tab", { name: /access/i }));

      await user.click(
        await screen.findByRole("combobox", { name: /grant to/i }),
      );
      await user.click(
        await screen.findByRole("option", { name: /acme analytics/i }),
      );
      await user.click(screen.getByRole("button", { name: /^grant$/i }));

      await waitFor(() =>
        expect(sent).toEqual({ workspace_id: "ws-1", tier: "use" }),
      );
    });

    it("asks for a principal before granting", async () => {
      const user = userEvent.setup();
      renderWithProviders({ initialRoute: ELASTIC });
      await user.click(await screen.findByRole("tab", { name: /access/i }));

      await user.click(screen.getByRole("button", { name: /^grant$/i }));
      expect(
        await screen.findByText(/pick a user or workspace/i),
      ).toBeInTheDocument();
    });
  });

  describe("revoking", () => {
    it("removes a grant", async () => {
      let deleted: string | null = null;
      server.use(
        http.get("/api/admin/agents/ag-5/access", () =>
          HttpResponse.json({
            agent_id: "ag-5",
            access_mode: "restricted",
            grants: [
              {
                id: "g-9",
                user_id: "u-1",
                user_name: "Ada Lovelace",
                workspace_id: null,
                workspace_name: null,
                tier: "use",
                created_at: "2026-07-29T00:00:00Z",
              },
            ],
            principals: [],
          }),
        ),
        http.delete("/api/admin/agents/ag-5/grants/:grantId", ({ params }) => {
          deleted = String(params.grantId);
          return new HttpResponse(null, { status: 204 });
        }),
      );
      const user = userEvent.setup();
      renderWithProviders({ initialRoute: ELASTIC });
      await user.click(await screen.findByRole("tab", { name: /access/i }));

      await user.click(
        await screen.findByRole("button", {
          name: /remove ada lovelace grant/i,
        }),
      );
      await waitFor(() => expect(deleted).toBe("g-9"));
    });

    it("labels a workspace grant as covering every member", async () => {
      server.use(
        http.get("/api/admin/agents/ag-5/access", () =>
          HttpResponse.json({
            agent_id: "ag-5",
            access_mode: "restricted",
            grants: [
              {
                id: "g-w",
                user_id: null,
                user_name: null,
                workspace_id: "ws-1",
                workspace_name: "Acme Analytics",
                tier: "use",
                created_at: "2026-07-29T00:00:00Z",
              },
            ],
            principals: [],
          }),
        ),
      );
      const user = userEvent.setup();
      renderWithProviders({ initialRoute: ELASTIC });
      await user.click(await screen.findByRole("tab", { name: /access/i }));

      const row = (await screen.findByText("Acme Analytics")).closest("div")!;
      expect(within(row).getByText(/every member/i)).toBeInTheDocument();
    });
  });
});
