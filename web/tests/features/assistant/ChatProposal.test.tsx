import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { configure, getConfig, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "@tests/utils";
import { describeChange } from "@/features/assistant/ChatProposal";
import type { DashboardFile } from "@/types/dashboard";

// The streamed reply is mirrored into an aria-live region for screen readers;
// text queries read what a sighted user sees.
const originalIgnore = getConfig().defaultIgnore;
beforeAll(() => {
  configure({ defaultIgnore: `${originalIgnore}, [aria-live]` });
});
afterAll(() => {
  configure({ defaultIgnore: originalIgnore });
});

const ANSWER =
  "I proposed a new title. Review it, then accept it as the draft or discard it.";

async function propose() {
  const user = userEvent.setup();
  const rendered = renderWithProviders({
    initialRoute: "/acme-analytics/dashboards",
  });
  await user.click(
    await screen.findByRole("button", { name: "Toggle AI assistant" }),
  );
  await screen.findByText("There are 42 events in the events table.");
  await user.type(screen.getByLabelText("Message"), "retitle the dashboard");
  await user.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText(ANSWER);
  // The turn is over when the conversation is read back.
  await screen.findByRole("button", { name: "Regenerate" });
  return { user, ...rendered };
}

describe("changes the assistant proposed, in its panel", () => {
  it("are a card where it proposed them, saying nothing is saved yet", async () => {
    await propose();

    // Named by the dashboard's current title, once it has loaded.
    const card = await screen.findByRole("region", {
      name: "Proposed changes to Sales overview",
    });
    expect(
      within(card).getByText("Retitle the dashboard for its weekly review."),
    ).toBeVisible();
    expect(
      await within(card).findByText(
        "Title changed: “Sales overview” → “Sales, weekly”",
      ),
    ).toBeVisible();
    expect(await within(card).findByText("Waiting for you")).toBeVisible();
    expect(
      card.compareDocumentPosition(screen.getByText(ANSWER)) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("open their review page", async () => {
    const { user, router } = await propose();
    // Named by the dashboard's current title, once it has loaded.
    const card = await screen.findByRole("region", {
      name: "Proposed changes to Sales overview",
    });

    await user.click(
      within(card).getByRole("link", { name: "Review changes" }),
    );

    expect(router.state.location.pathname).toBe(
      "/acme-analytics/dashboard-proposals/proposal-1",
    );
    expect(
      await screen.findByRole("heading", {
        name: "Proposed changes to Sales overview",
      }),
    ).toBeVisible();
  });
});

describe("describeChange", () => {
  it.each([
    [{ path: "title", change: "changed" }, "Title changed"],
    [
      { path: "widgets.by_country", change: "added" },
      "Widget by_country added",
    ],
    [{ path: "parameters.region", change: "removed" }, "Filter region removed"],
    [{ path: "layout", change: "changed" }, "Layout changed"],
  ] as const)("reads %o as %s", (change, text) => {
    expect(describeChange(change)).toBe(text);
  });

  const before = {
    version: 1,
    name: "sales",
    title: "Sales overview",
    rows: [],
  } as DashboardFile;

  it.each([
    [
      { path: "title", change: "changed" },
      { ...before, title: "Sales, weekly" },
      "Title changed: “Sales overview” → “Sales, weekly”",
    ],
    [
      { path: "description", change: "added" },
      { ...before, description: "Paid orders only." },
      "Description added: “Paid orders only.”",
    ],
    [
      { path: "widgets.by_country", change: "added" },
      before,
      "Widget by_country added",
    ],
  ] as const)(
    "says what %o was and becomes, given both files",
    (change, after, text) => {
      expect(describeChange(change, before, after)).toBe(text);
    },
  );

  it("says only that the text changed when both files agree", () => {
    // After acceptance the draft already holds the new title.
    expect(
      describeChange({ path: "title", change: "changed" }, before, before),
    ).toBe("Title changed");
  });
});
