import { get, post, patch, del, getAllPages } from "./client";
import { readSSE } from "./sse";
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
    getAllPages<Conversation>(`/workspaces/${ws}/assistant/conversations`),

  createConversation: (ws: string, title?: string) =>
    post<Conversation>(`/workspaces/${ws}/assistant/conversations`, {
      title: title ?? null,
    }),

  getConversation: (ws: string, id: string) =>
    get<ConversationDetail>(`/workspaces/${ws}/assistant/conversations/${id}`),

  renameConversation: (ws: string, id: string, title: string) =>
    patch<Conversation>(`/workspaces/${ws}/assistant/conversations/${id}`, {
      title,
    }),

  deleteConversation: (ws: string, id: string) =>
    del(`/workspaces/${ws}/assistant/conversations/${id}`),
};

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
        page_context:
          opts?.editorSql != null || opts?.selectionSql != null
            ? {
                page: "worksheet",
                sql: opts.editorSql ?? null,
                selection: opts.selectionSql ?? null,
              }
            : null,
      }),
      signal: opts?.signal,
    },
  );
  yield* readSSE<AssistantFrame>(res, opts?.signal);
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
  yield* readSSE<AssistantFrame>(res, opts?.signal);
}
