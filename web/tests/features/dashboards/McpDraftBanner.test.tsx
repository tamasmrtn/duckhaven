import { describe, expect, it, vi } from "vitest";
import { renderWithProviders, screen } from "@tests/utils";
import { DASHBOARDS } from "@/mock/fixtures/dashboards";
import type { Dashboard } from "@/types/dashboard";

// The editor's Monaco does not run in jsdom: a textarea stands in for it, and
// the YAML language is reported loaded.
vi.mock("@monaco-editor/react", () => ({
  Editor: ({ value }: { value: string }) => (
    <textarea aria-label="Dashboard file" value={value} readOnly />
  ),
  default: () => null,
}));
vi.mock("@/features/dashboards/editor/yamlLoader", () => ({
  useDashboardYamlReady: () => true,
}));

const SLOW = { timeout: 15_000 };
const ROUTE = "/acme-analytics/dashboards";

/** Sales overview, with its draft last saved by an agent over MCP. */
function draftedOverMcp(changes: Partial<Dashboard> = {}) {
  Object.assign(
    DASHBOARDS.find((d) => d.name === "sales-overview")!,
    {
      updated_via: "mcp",
      client: "claude-code/2.1.0",
      ...changes,
    },
  );
}

describe("a draft an agent saved over MCP", SLOW, () => {
  it("says so in the editor, with the client and as you", async () => {
    draftedOverMcp();
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });

    const banner = await screen.findByText(/Drafted over MCP/);

    expect(banner.closest("span")).toHaveTextContent(
      "Drafted over MCP by claude-code/2.1.0 as you. An agent saved this draft",
    );
  });

  it("names the person it acted as, to an owner on the dashboard", async () => {
    draftedOverMcp({ updated_by: "u-2", updated_by_name: "Ann Lee" });
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview` });

    expect(
      (await screen.findByText(/Drafted over MCP/)).closest("span"),
    ).toHaveTextContent("by claude-code/2.1.0 as Ann Lee.");
  });

  it("says nothing once the draft is gone", async () => {
    draftedOverMcp({ has_draft: false });
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview` });

    await screen.findByRole("heading", { name: "Sales overview" });
    expect(screen.queryByText(/Drafted over MCP/)).toBeNull();
  });

  it("says nothing of a draft saved in the app", async () => {
    renderWithProviders({ initialRoute: `${ROUTE}/sales-overview/edit` });

    await screen.findByText(/Editing the draft/);
    expect(screen.queryByText(/Drafted over MCP/)).toBeNull();
  });
});
