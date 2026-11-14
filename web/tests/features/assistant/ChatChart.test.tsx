import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import {
  configure,
  getConfig,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@tests/utils";

// Adding a chart opens the dashboard editor. Monaco does not run in jsdom: a
// textarea stands in for it, and the YAML language is reported loaded.
vi.mock("@monaco-editor/react", () => ({
  Editor: ({ value }: { value: string }) => (
    <textarea aria-label="Dashboard file" value={value} readOnly />
  ),
  default: () => null,
}));
vi.mock("@/features/dashboards/editor/yamlLoader", () => ({
  useDashboardYamlReady: () => true,
}));

// The streamed reply is mirrored into an aria-live region for screen readers;
// text queries read what a sighted user sees.
const originalIgnore = getConfig().defaultIgnore;
beforeAll(() => {
  configure({ defaultIgnore: `${originalIgnore}, [aria-live]` });
});
afterAll(() => {
  configure({ defaultIgnore: originalIgnore });
});

const ANSWER = "Revenue is highest in NL, at 18,420.00.";

async function ask(prompt: string) {
  const user = userEvent.setup();
  const rendered = renderWithProviders({
    initialRoute: "/acme-analytics/dashboards",
  });
  await user.click(
    await screen.findByRole("button", { name: "Toggle AI assistant" }),
  );
  await screen.findByText("There are 42 events in the events table.");
  await user.type(screen.getByLabelText("Message"), prompt);
  await user.click(screen.getByRole("button", { name: "Send" }));
  return { user, ...rendered };
}

/** The chart the mock assistant draws, once the turn is read back. */
async function chartCard() {
  await screen.findByText(ANSWER);
  // The turn is over when the conversation is read back.
  await screen.findByRole("button", { name: "Regenerate" });
  return screen.getByRole("region", { name: "Chart: Revenue by country" });
}

describe("a chart in the assistant panel", () => {
  it("is drawn where the assistant drew it, and kept with the conversation", async () => {
    const { user } = await ask("chart revenue by country");
    const card = await chartCard();

    expect(
      within(card).getByRole("img", { name: /^Revenue by country\./ }),
    ).toBeInTheDocument();
    // Before the words about it, as the assistant wrote them.
    expect(
      card.compareDocumentPosition(screen.getByText(ANSWER)) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // Its rows, and the SQL they came from, are a menu away; a chart in the
    // chat has no page of its own to link to.
    await user.click(
      within(card).getByRole("button", {
        name: "Actions for Revenue by country",
      }),
    );
    expect(
      screen.queryByRole("menuitem", { name: "Copy link" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "View as table" }));
    expect(within(card).getByRole("cell", { name: "NL" })).toBeInTheDocument();
  });

  it("goes on a dashboard edited in the app, unsaved in its editor", async () => {
    const { user, router } = await ask("chart revenue by country");
    const card = await chartCard();

    await user.click(
      within(card).getByRole("button", { name: /Add to dashboard/ }),
    );
    await screen.findByRole("menuitem", { name: "Sales overview" });
    expect(
      screen.getByRole("menuitem", { name: "Churn review" }),
    ).toBeInTheDocument();
    // Managed in git, so it changes only through its file there.
    expect(
      screen.queryByRole("menuitem", { name: "Finance weekly" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "Sales overview" }));

    const file = (await screen.findByRole("textbox", {
      name: "Dashboard file",
    })) as HTMLTextAreaElement;
    await waitFor(() => expect(file.value).toContain("id: by_country_2"));
    expect(file.value).toContain("sql: |-");
    expect(
      screen.getByText(/The assistant's chart is at the end of the file/),
    ).toBeInTheDocument();
    expect(router.state.location.pathname).toBe(
      "/acme-analytics/dashboards/sales-overview/edit",
    );
    // Taken out of the history entry, so reloading adds nothing.
    await waitFor(() =>
      expect(router.state.location.state.addWidget).toBeUndefined(),
    );
  });

  it("starts a new dashboard that holds only it", async () => {
    const { user } = await ask("chart revenue by country");
    const card = await chartCard();

    await user.click(
      within(card).getByRole("button", { name: /Add to dashboard/ }),
    );
    await user.click(
      await screen.findByRole("menuitem", { name: "New dashboard…" }),
    );
    const dialog = await screen.findByRole("dialog", { name: "New dashboard" });
    expect(within(dialog).getByLabelText("Title")).toHaveValue(
      "Revenue by country",
    );
    await user.click(within(dialog).getByRole("button", { name: "Create" }));

    const file = (await screen.findByRole("textbox", {
      name: "Dashboard file",
    })) as HTMLTextAreaElement;
    expect(file.value).toContain("name: revenue-by-country");
    expect(file.value).toContain("- id: by_country\n        type: bar");
    expect(file.value).not.toContain("type: text");
  });
});
