import { http, HttpResponse } from "msw";
import {
  ASSISTANT_ENABLED,
  CONVERSATIONS,
  type MockConversation,
} from "../fixtures/assistant";
import { findWorkspace } from "../fixtures/workspaces";
import { nextId } from "../lib/seed";
import { httpError } from "../lib/errors";

function sse(frames: object[]) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      // Small delay so the stream doesn't resolve synchronously — lets tests
      // observe the interval where the turn is streaming (e.g. the optimistic
      // echo of the user's just-sent message).
      setTimeout(() => {
        for (const frame of frames) {
          controller.enqueue(
            encoder.encode(`data: ${JSON.stringify(frame)}\n\n`),
          );
        }
        controller.close();
      }, 20);
    },
  });
  return new HttpResponse(stream, {
    headers: { "Content-Type": "text/event-stream" },
  });
}

function publicView(c: MockConversation) {
  const { transcript: _t, tool_calls: _tc, ...rest } = c;
  void _t;
  void _tc;
  return rest;
}

export const assistantHandlers = [
  http.get("/api/workspaces/:ws/assistant/status", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    return HttpResponse.json({ enabled: ASSISTANT_ENABLED });
  }),

  http.get("/api/workspaces/:ws/assistant/conversations", ({ params }) => {
    const ws = findWorkspace(params.ws as string);
    if (!ws) return httpError(404, "Workspace not found");
    return HttpResponse.json(
      CONVERSATIONS.filter((c) => c.workspace_id === ws.id).map(publicView),
    );
  }),

  http.post(
    "/api/workspaces/:ws/assistant/conversations",
    async ({ params, request }) => {
      const ws = findWorkspace(params.ws as string);
      if (!ws) return httpError(404, "Workspace not found");
      const body = (await request.json()) as { title?: string | null };
      const conv: MockConversation = {
        id: nextId("conv"),
        workspace_id: ws.id,
        title: body.title || "New conversation",
        total_input_tokens: 0,
        total_output_tokens: 0,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        history_truncated: false,
        transcript: [],
        tool_calls: [],
      };
      CONVERSATIONS.unshift(conv);
      return HttpResponse.json(publicView(conv), { status: 201 });
    },
  ),

  http.get("/api/workspaces/:ws/assistant/conversations/:id", ({ params }) => {
    const conv = CONVERSATIONS.find((c) => c.id === params.id);
    if (!conv) return httpError(404, "Conversation not found");
    return HttpResponse.json(conv);
  }),

  http.patch(
    "/api/workspaces/:ws/assistant/conversations/:id",
    async ({ params, request }) => {
      const conv = CONVERSATIONS.find((c) => c.id === params.id);
      if (!conv) return httpError(404, "Conversation not found");
      const { title } = (await request.json()) as { title: string };
      conv.title = title;
      return HttpResponse.json(publicView(conv));
    },
  ),

  http.delete(
    "/api/workspaces/:ws/assistant/conversations/:id",
    ({ params }) => {
      const idx = CONVERSATIONS.findIndex((c) => c.id === params.id);
      if (idx === -1) return httpError(404, "Conversation not found");
      CONVERSATIONS.splice(idx, 1);
      return new HttpResponse(null, { status: 204 });
    },
  ),

  http.post(
    "/api/workspaces/:ws/assistant/conversations/:id/messages",
    async ({ params, request }) => {
      const conv = CONVERSATIONS.find((c) => c.id === params.id);
      if (!conv) return httpError(404, "Conversation not found");
      const { prompt, selection_sql } = (await request.json()) as {
        prompt: string;
        selection_sql?: string | null;
      };
      conv.transcript.push({ role: "user", text: prompt, sql: null });

      // A stream that emits a token then never closes, so the turn stays
      // "streaming" until the client aborts it (exercises the Stop button).
      if (/\bhang\b/i.test(prompt)) {
        const encoder = new TextEncoder();
        const stream = new ReadableStream({
          start(controller) {
            controller.enqueue(
              encoder.encode(
                `data: ${JSON.stringify({ type: "token", text: "thinking…" })}\n\n`,
              ),
            );
            // Intentionally never closed.
          },
        });
        return new HttpResponse(stream, {
          headers: { "Content-Type": "text/event-stream" },
        });
      }

      // Simulate a write proposal that needs approval.
      if (/\b(delete|drop|update|insert)\b/i.test(prompt)) {
        conv.tool_calls.push({
          id: nextId("tc"),
          tool: "run_sql",
          args: { sql: "DELETE FROM events" },
          status: "approval_required",
          detail: null,
          query_id: null,
          latency_ms: 3,
          created_at: new Date().toISOString(),
        });
        return sse([
          {
            type: "approval_required",
            tool_call_id: "call-1",
            tool: "run_sql",
            sql: "DELETE FROM events",
          },
        ]);
      }

      // Simulate proposing an edit with two separate, non-adjacent changed
      // lines — exercises the multi-hunk review flow.
      if (/\bmulti-hunk\b/i.test(prompt)) {
        const proposed = [
          "SELECT",
          "  date_trunc('day', event_time) d,",
          "  count(*) n",
          "FROM raw.events",
          "WHERE event_time >= '2026-06-01'",
          "GROUP BY 1",
          "ORDER BY 1 DESC;",
        ].join("\n");
        conv.transcript.push({
          role: "assistant",
          text: "I updated two parts of your query.",
          sql: proposed,
        });
        return sse([
          {
            type: "propose_edit",
            sql: proposed,
            explanation: "widen the date filter and sort newest first",
            scoped: Boolean(selection_sql),
          },
          { type: "token", text: "I updated two parts of your query." },
          {
            type: "done",
            message_id: nextId("msg"),
            usage: { input: 8, output: 6 },
          },
        ]);
      }

      // Simulate proposing an editor edit.
      if (/\b(write|edit|filter|add|column|query)\b/i.test(prompt)) {
        const proposed = "SELECT * FROM events LIMIT 10";
        conv.transcript.push({
          role: "assistant",
          text: "I proposed a query in your editor.",
          sql: proposed,
        });
        return sse([
          {
            type: "propose_edit",
            sql: proposed,
            explanation: "select recent events",
            scoped: Boolean(selection_sql),
          },
          { type: "token", text: "I proposed a query in your editor." },
          {
            type: "done",
            message_id: nextId("msg"),
            usage: { input: 8, output: 6 },
          },
        ]);
      }

      const answer = "Here is what I found.";
      conv.transcript.push({ role: "assistant", text: answer, sql: null });
      return sse([
        { type: "token", text: "Here is " },
        { type: "token", text: "what I found." },
        {
          type: "done",
          message_id: nextId("msg"),
          usage: { input: 10, output: 5 },
        },
      ]);
    },
  ),

  http.post(
    "/api/workspaces/:ws/assistant/conversations/:id/approvals",
    async ({ params, request }) => {
      const conv = CONVERSATIONS.find((c) => c.id === params.id);
      if (!conv) return httpError(404, "Conversation not found");
      const { approved } = (await request.json()) as { approved: boolean };
      const answer = approved
        ? "Done — the write ran."
        : "Okay, I won't run it.";
      conv.transcript.push({ role: "assistant", text: answer, sql: null });
      return sse([
        { type: "token", text: answer },
        {
          type: "done",
          message_id: nextId("msg"),
          usage: { input: 4, output: 4 },
        },
      ]);
    },
  ),
];
