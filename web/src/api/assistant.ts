import { get, post, del } from "./client";
import { ApiError } from "./client";
import type {
  AssistantFrame,
  AssistantStatus,
  Conversation,
  ConversationDetail,
} from "@/types/assistant";

export const assistantApi = {
  status: (ws: string) =>
    get<AssistantStatus>(`/workspaces/${ws}/assistant/status`),

  listConversations: (ws: string) =>
    get<Conversation[]>(`/workspaces/${ws}/assistant/conversations`),

  createConversation: (ws: string, title?: string) =>
    post<Conversation>(`/workspaces/${ws}/assistant/conversations`, {
      title: title ?? null,
    }),

  getConversation: (ws: string, id: string) =>
    get<ConversationDetail>(`/workspaces/${ws}/assistant/conversations/${id}`),

  deleteConversation: (ws: string, id: string) =>
    del(`/workspaces/${ws}/assistant/conversations/${id}`),
};

/** Read a `text/event-stream` body and yield decoded assistant frames. */
async function* readSSE(
  res: Response,
  signal?: AbortSignal,
): AsyncGenerator<AssistantFrame, void, unknown> {
  if (!res.ok || !res.body) {
    let message = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // ignore
    }
    throw new ApiError(res.status, message);
  }
  const reader = res.body.getReader();
  // Cancelling the reader unblocks a pending read() so Stop takes effect
  // immediately, even where aborting the fetch doesn't reject the read.
  const onAbort = () => void reader.cancel().catch(() => {});
  signal?.addEventListener("abort", onAbort);
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        for (const line of block.split("\n")) {
          if (line.startsWith("data: ")) {
            yield JSON.parse(line.slice(6)) as AssistantFrame;
          }
        }
      }
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
  }
}

/** Send a user turn; yields streamed frames as the assistant works. */
export async function* streamMessage(
  ws: string,
  conversationId: string,
  prompt: string,
  opts?: {
    catalog?: string | null;
    editorSql?: string | null;
    selectionSql?: string | null;
    signal?: AbortSignal;
  },
): AsyncGenerator<AssistantFrame, void, unknown> {
  const res = await fetch(
    `/api/workspaces/${ws}/assistant/conversations/${conversationId}/messages`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        catalog: opts?.catalog ?? null,
        editor_sql: opts?.editorSql ?? null,
        selection_sql: opts?.selectionSql ?? null,
      }),
      signal: opts?.signal,
    },
  );
  yield* readSSE(res, opts?.signal);
}

/** Approve or deny a pending write; yields the resumed turn's frames. */
export async function* streamApproval(
  ws: string,
  conversationId: string,
  toolCallId: string,
  approved: boolean,
  opts?: { reason?: string; signal?: AbortSignal },
): AsyncGenerator<AssistantFrame, void, unknown> {
  const res = await fetch(
    `/api/workspaces/${ws}/assistant/conversations/${conversationId}/approvals`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tool_call_id: toolCallId,
        approved,
        reason: opts?.reason ?? null,
      }),
      signal: opts?.signal,
    },
  );
  yield* readSSE(res, opts?.signal);
}
