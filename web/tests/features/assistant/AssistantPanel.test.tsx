import { describe, it, expect } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "@tests/utils";
import { server } from "@tests/mock/server";
import { CONVERSATIONS, setAssistantEnabled } from "@/mock/fixtures/assistant";

// The panel is a right-side dock opened from the top bar; render a workspace page
// so the worksheet editor bridge is live for the propose-edit test.
const ROUTE = "/acme-analytics/worksheets";

async function openPanel(user: ReturnType<typeof userEvent.setup>) {
  const toggle = await screen.findByRole("button", {
    name: "Toggle AI assistant",
  });
  await user.click(toggle);
}

describe("AssistantPanel", () => {
  it("opens from the top bar and shows the seeded conversation", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);

    expect(
      await screen.findByRole("complementary", { name: "AI assistant" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("There are 42 events in the events table."),
    ).toBeInTheDocument();
  });

  it("shows token usage and the SQL a turn ran inline under the answer", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    // Token usage badge in the header (seeded conversation: 120 in / 60 out).
    expect(screen.getByText("120 in · 60 out")).toBeInTheDocument();
    // The SQL that produced the answer is shown inline, not just in Activity.
    expect(screen.getByText("SELECT count(*) FROM events")).toBeInTheDocument();
  });

  it("shows a dismissible notice when the conversation history is truncated", async () => {
    const conv = CONVERSATIONS.find((c) => c.id === "conv-1")!;
    conv.history_truncated = true;
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    const notice = screen.getByText(
      "This conversation is long — earlier messages are no longer part of its context.",
    );
    expect(notice).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Dismiss notice" }));
    expect(notice).not.toBeInTheDocument();
  });

  it("does not show the truncation notice for a conversation under the cap", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    expect(
      screen.queryByText(
        "This conversation is long — earlier messages are no longer part of its context.",
      ),
    ).not.toBeInTheDocument();
  });

  it("searches and switches conversations from the history list", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.click(
      screen.getByRole("button", { name: "Conversation history" }),
    );
    expect(screen.getByText("Exploring events")).toBeInTheDocument();
    expect(screen.getByText("Revenue check")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Search conversations"), "revenue");
    expect(screen.queryByText("Exploring events")).not.toBeInTheDocument();
    const target = screen.getByText("Revenue check");
    await user.click(target);

    // Switching conversations closes the popover and loads the other thread.
    await waitFor(() =>
      expect(screen.queryByText("Revenue check")).not.toBeInTheDocument(),
    );
    expect(
      screen.queryByText("There are 42 events in the events table."),
    ).not.toBeInTheDocument();
  });

  it("renames a conversation via the history list", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.click(
      screen.getByRole("button", { name: "Conversation history" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Actions for Exploring events" }),
    );
    await user.click(screen.getByRole("menuitem", { name: "Rename" }));

    const titleInput = screen.getByLabelText("Conversation title");
    await user.clear(titleInput);
    await user.type(titleInput, "Event volume investigation{Enter}");
    await waitFor(() =>
      expect(
        screen.queryByLabelText("Conversation title"),
      ).not.toBeInTheDocument(),
    );

    // Reopen the list to confirm the rename was persisted.
    await user.click(
      screen.getByRole("button", { name: "Conversation history" }),
    );
    expect(
      await screen.findByText("Event volume investigation"),
    ).toBeInTheDocument();
  });

  it("deletes a conversation and falls back to another", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.click(
      screen.getByRole("button", { name: "Conversation history" }),
    );
    await user.click(
      screen.getByRole("button", { name: "Actions for Exploring events" }),
    );
    await user.click(screen.getByRole("menuitem", { name: "Delete" }));

    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    // The deleted conversation is gone; the panel falls back to the other one.
    await waitFor(() =>
      expect(
        screen.queryByText("There are 42 events in the events table."),
      ).not.toBeInTheDocument(),
    );
  });

  it("shows a follow-up chip linking to the last query's result", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    const link = screen.getByRole("link", { name: "View full result" });
    expect(link).toHaveAttribute("href", "/acme-analytics/queries/q-1");
    // The follow-up chip focuses the composer instead of submitting anything.
    await user.click(screen.getByRole("button", { name: "Ask a follow-up" }));
    expect(screen.getByLabelText("Message")).toHaveFocus();
  });

  it("shows an 'open in Catalog' chip for a table the last query touched", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    const link = screen.getByRole("link", { name: "Open events in Catalog" });
    expect(link).toHaveAttribute(
      "href",
      "/acme-analytics/catalog/acme_analytics/raw/events",
    );
  });

  it("shows no catalog chip when the last query touched no resolvable table", async () => {
    const conv = CONVERSATIONS.find((c) => c.id === "conv-1")!;
    conv.tool_calls[0].tables = null;
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    expect(
      screen.queryByRole("link", { name: /open .* in catalog/i }),
    ).not.toBeInTheDocument();
  });

  it("shows one chip per distinct table when a query touches several", async () => {
    const conv = CONVERSATIONS.find((c) => c.id === "conv-1")!;
    conv.tool_calls[0].tables = [
      { catalog: "acme_analytics", schema_name: "raw", table: "events" },
      { catalog: "acme_analytics", schema_name: "raw", table: "users" },
    ];
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    expect(
      screen.getByRole("link", { name: "Open events in Catalog" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open users in Catalog" }),
    ).toBeInTheDocument();
  });

  it("suggests catalog-scoped starter prompts on a fresh conversation", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: "/acme-research/worksheets" });
    await openPanel(user);

    const starter = await screen.findByRole("button", {
      name: "What tables are in acme_research?",
    });
    await user.click(starter);

    // Clicking a starter prompt submits it immediately, like typing and sending;
    // the starter chips disappear once the turn is under way.
    expect(
      await screen.findByText("Here is what I found."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "What tables are in acme_research?",
      }),
    ).not.toBeInTheDocument();
  });

  it("collapses the Activity trace by default and expands it on click", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    // Collapsed: a count is shown but the tool rows are hidden.
    const toggle = screen.getByRole("button", { name: /Activity \(1\)/ });
    expect(screen.queryByText("run_sql")).not.toBeInTheDocument();

    await user.click(toggle);
    expect(screen.getByText("run_sql")).toBeInTheDocument();
  });

  it("echoes the user's message immediately, before the reply streams in", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    const probe = "unique probe message one two three";
    await user.type(screen.getByLabelText("Message"), probe);
    await user.click(screen.getByRole("button", { name: "Send" }));

    // The user's message is visible right away (optimistic echo) while the
    // assistant reply is still streaming and not yet shown.
    expect(screen.getByText(probe)).toBeInTheDocument();
    expect(screen.queryByText("Here is what I found.")).not.toBeInTheDocument();

    // The reply then arrives, and the user's message remains (now persisted).
    expect(
      await screen.findByText("Here is what I found."),
    ).toBeInTheDocument();
    expect(screen.getByText(probe)).toBeInTheDocument();
  });

  it("streams an answer for a new message", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.type(screen.getByLabelText("Message"), "how many rows total?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(
      await screen.findByText("Here is what I found."),
    ).toBeInTheDocument();
  });

  it("sends the worksheet's active catalog with every turn", async () => {
    let capturedBody: { catalog?: string | null } | null = null;
    server.use(
      http.post(
        "/api/workspaces/:ws/assistant/conversations/:id/messages",
        async ({ request }) => {
          capturedBody = (await request.json()) as { catalog?: string | null };
          return new HttpResponse(
            `data: ${JSON.stringify({ type: "token", text: "ok" })}\n\ndata: ${JSON.stringify(
              {
                type: "done",
                message_id: "msg-x",
                usage: { input: 1, output: 1 },
              },
            )}\n\n`,
            { headers: { "Content-Type": "text/event-stream" } },
          );
        },
      ),
    );

    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.type(screen.getByLabelText("Message"), "how many rows?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(capturedBody).not.toBeNull());
    expect(capturedBody?.catalog).toBe("acme_analytics");
  });

  it("shows an inline Retry on error and resends the same prompt on click", async () => {
    let attempt = 0;
    server.use(
      http.post(
        "/api/workspaces/:ws/assistant/conversations/:id/messages",
        async ({ params, request }) => {
          attempt += 1;
          if (attempt === 1) {
            return new HttpResponse(
              `data: ${JSON.stringify({ type: "error", message: "The assistant hit an internal error." })}\n\n`,
              { headers: { "Content-Type": "text/event-stream" } },
            );
          }
          const conv = CONVERSATIONS.find((c) => c.id === params.id);
          const { prompt } = (await request.json()) as { prompt: string };
          conv?.transcript.push({ role: "user", text: prompt, sql: null });
          conv?.transcript.push({
            role: "assistant",
            text: "Recovered.",
            sql: null,
          });
          return new HttpResponse(
            `data: ${JSON.stringify({ type: "token", text: "Recovered." })}\n\ndata: ${JSON.stringify(
              {
                type: "done",
                message_id: "msg-retry",
                usage: { input: 1, output: 1 },
              },
            )}\n\n`,
            { headers: { "Content-Type": "text/event-stream" } },
          );
        },
      ),
    );

    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.type(screen.getByLabelText("Message"), "will this fail?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(
      await screen.findByText("The assistant hit an internal error."),
    ).toBeInTheDocument();
    // The failed prompt stays visible, anchoring the error to what caused it.
    expect(screen.getByText("will this fail?")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Recovered.")).toBeInTheDocument();
    expect(
      screen.queryByText("The assistant hit an internal error."),
    ).not.toBeInTheDocument();
    expect(attempt).toBe(2);
  });

  it("regenerates the last answer as a new turn", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.type(screen.getByLabelText("Message"), "how many rows total?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await screen.findAllByText("Here is what I found.");

    await user.click(screen.getByRole("button", { name: "Regenerate" }));

    // Regenerating resends the same prompt as a new turn; both replies remain.
    await waitFor(() =>
      expect(screen.getAllByText("Here is what I found.")).toHaveLength(2),
    );
    expect(screen.getAllByText("how many rows total?")).toHaveLength(2);
  });

  it("stops a streaming turn: discards the partial and offers Retry", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    // "hang" makes the mock stream a token then stay open until aborted.
    await user.type(screen.getByLabelText("Message"), "hang please");
    await user.click(screen.getByRole("button", { name: "Send" }));

    // The partial reply streams in (shown both as a bubble and, throttled, in
    // an aria-live region for screen readers, so query for either match).
    expect(await screen.findAllByText("thinking…")).not.toHaveLength(0);
    const stop = await screen.findByRole("button", { name: "Stop" });
    expect(
      screen.queryByRole("button", { name: "Send" }),
    ).not.toBeInTheDocument();

    await user.click(stop);

    // The server discards the turn, so the partial is cleared and a Stopped
    // note + Retry appears; the composer (Send) returns and the message stays.
    await waitFor(() =>
      expect(screen.queryByText("thinking…")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("Stopped.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "Send" }),
    ).toBeInTheDocument();
    expect(screen.getByText("hang please")).toBeInTheDocument();
  });

  it("does not render half-parsed markdown while a reply streams", async () => {
    // A stream the test drives frame by frame, so the assertion lands on a
    // known partial state instead of racing the transport.
    const encoder = new TextEncoder();
    let push!: (frame: object) => void;
    let finish!: () => void;
    server.use(
      http.post(
        "/api/workspaces/:ws/assistant/conversations/:id/messages",
        () => {
          const body = new ReadableStream({
            start(controller) {
              push = (frame) =>
                controller.enqueue(
                  encoder.encode(`data: ${JSON.stringify(frame)}\n\n`),
                );
              finish = () => controller.close();
            },
          });
          return new HttpResponse(body, {
            headers: { "Content-Type": "text/event-stream" },
          });
        },
      ),
    );

    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.type(screen.getByLabelText("Message"), "biggest tables?");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(push).toBeDefined());

    // A bold marker that has not closed yet. Asserting on the *presence* of the
    // stabilized text is what makes this bite: an unfixed panel renders the
    // literal "The **thr", so there is no element reading "The thr" to find,
    // and findByText waits for the frame rather than racing it.
    push({ type: "token", text: "The **thr" });
    expect(await screen.findByText("The thr")).toBeInTheDocument();

    // Once the answer completes, nothing the stabilizer held back is lost.
    push({
      type: "token",
      text: "ee** biggest tables:\n\n| Table | Rows |\n|---|---:|\n| events | 42 |\n",
    });
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(
      await screen.findByRole("columnheader", { name: "Table" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "events" })).toBeInTheDocument();
    expect(screen.getByText("three").tagName).toBe("STRONG");

    push({
      type: "done",
      message_id: "msg-md",
      usage: { input: 1, output: 1 },
    });
    finish();
  });

  it("clears a stale error/pending bubble when switching conversations", async () => {
    server.use(
      http.post(
        "/api/workspaces/:ws/assistant/conversations/:id/messages",
        () =>
          new HttpResponse(
            `data: ${JSON.stringify({ type: "error", message: "The assistant hit an internal error." })}\n\n`,
            { headers: { "Content-Type": "text/event-stream" } },
          ),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    // Error a turn in the first conversation.
    await user.type(screen.getByLabelText("Message"), "unique failing prompt");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(
      await screen.findByText("The assistant hit an internal error."),
    ).toBeInTheDocument();
    expect(screen.getByText("unique failing prompt")).toBeInTheDocument();

    // Switch to another conversation.
    await user.click(
      screen.getByRole("button", { name: "Conversation history" }),
    );
    await user.click(screen.getByText("Revenue check"));

    // The other conversation shows none of the first's transient state.
    await waitFor(() =>
      expect(
        screen.queryByText("The assistant hit an internal error."),
      ).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("unique failing prompt")).not.toBeInTheDocument();
  });

  it("shows an inline error when the conversation list fails to load", async () => {
    server.use(
      http.get("/api/workspaces/:ws/assistant/conversations", () =>
        HttpResponse.json({ detail: "boom" }, { status: 500 }),
      ),
    );

    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);

    expect(
      await screen.findByText(/Couldn't load your conversations/i),
    ).toBeInTheDocument();
  });

  it("shows starter prompts for an existing but empty conversation", async () => {
    server.use(
      http.get(
        "/api/workspaces/:ws/assistant/conversations/:id",
        ({ params }) => {
          const conv = CONVERSATIONS.find((c) => c.id === params.id);
          if (!conv) return new HttpResponse(null, { status: 404 });
          return HttpResponse.json({
            ...conv,
            transcript: [],
            tool_calls: [],
          });
        },
      ),
    );

    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);

    // A selected conversation with an empty transcript falls back to the empty
    // state's starter prompts instead of rendering a blank thread. Getting there
    // needs two sequential fetches (the conversation list, then its detail), so
    // give this one more room than the default 1s findByRole timeout.
    expect(
      await screen.findByRole(
        "button",
        { name: "What tables are in acme_analytics?" },
        { timeout: 3000 },
      ),
    ).toBeInTheDocument();
  });

  it("prompts for approval when the assistant proposes a write", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.type(screen.getByLabelText("Message"), "delete the events");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Approve write?")).toBeInTheDocument();
    expect(within(dialog).getByText("DELETE FROM events")).toBeInTheDocument();
  });

  it("shows a turned-off notice and disables input when the assistant is off", async () => {
    setAssistantEnabled(false);
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);

    expect(
      await screen.findByText("Assistant is turned off"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/ask a DuckHaven admin to turn it on/i),
    ).toBeInTheDocument();
    // The composer is present but disabled.
    expect(screen.getByLabelText("Message")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
  });

  it("proposes an editor edit that the user can accept", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.type(screen.getByLabelText("Message"), "write me a query");
    await user.click(screen.getByRole("button", { name: "Send" }));

    // The worksheet shows the AI-proposal bar with accept/reject.
    expect(
      await screen.findByText(/Assistant proposed changes/),
    ).toBeInTheDocument();
    const accept = screen.getByRole("button", { name: /Accept/ });
    await user.click(accept);
    await waitFor(() =>
      expect(
        screen.queryByText(/Assistant proposed changes/),
      ).not.toBeInTheDocument(),
    );
  });

  it("accepts a proposal spanning multiple changed blocks as a single unit", async () => {
    const user = userEvent.setup();
    renderWithProviders({ initialRoute: ROUTE });
    await openPanel(user);
    await screen.findByText("There are 42 events in the events table.");

    await user.type(screen.getByLabelText("Message"), "multi-hunk edit please");
    await user.click(screen.getByRole("button", { name: "Send" }));

    // Multiple changed blocks still resolve through the same single
    // Accept/Reject bar — there's no per-hunk control.
    expect(
      await screen.findByText(/Assistant proposed changes/),
    ).toBeInTheDocument();
    const accept = screen.getByRole("button", { name: /Accept/ });
    await user.click(accept);
    await waitFor(() =>
      expect(
        screen.queryByText(/Assistant proposed changes/),
      ).not.toBeInTheDocument(),
    );
  });
});
