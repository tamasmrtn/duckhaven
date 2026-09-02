export interface AssistantStatus {
  enabled: boolean;
  // Why the assistant can't be used here, or "ok". The two unusable states have
  // different fixes — re-enable the account vs. add it to this workspace — so the
  // panel can name the right one without starting a turn.
  availability:
    "disabled" | "account_unavailable" | "no_workspace_access" | "ok";
}

export interface Conversation {
  id: string;
  workspace_id: string;
  title: string;
  total_input_tokens: number;
  total_output_tokens: number;
  created_at: string;
  updated_at: string;
}

export interface DocSource {
  path: string;
  title: string;
  // The published page for the version this deployment runs — not the latest
  // docs, so the link shows what the assistant actually read. Null when the
  // page is no longer in the shipped index.
  url: string | null;
}

export interface TranscriptItem {
  role: "user" | "assistant";
  text: string;
  // The SQL this turn ran or proposed, if any (attributed server-side).
  sql: string | null;
  // Documentation pages this turn opened. Null on most turns.
  sources: DocSource[] | null;
}

export interface AssistantToolCallTableRef {
  catalog: string;
  schema_name: string;
  table: string;
}

export interface AssistantToolCall {
  id: string;
  tool: string;
  args: Record<string, unknown> | null;
  status: string;
  detail: string | null;
  query_id: string | null;
  latency_ms: number | null;
  tables: AssistantToolCallTableRef[] | null;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  transcript: TranscriptItem[];
  tool_calls: AssistantToolCall[];
  history_truncated: boolean;
}

// Server-sent events streamed from a turn.
export type AssistantFrame =
  // `start` marks the first token of a model response, which is the message
  // boundary the settled transcript will use. Absent on every other token.
  | { type: "token"; text: string; start?: boolean }
  | { type: "tool_call"; tool: string; args: unknown }
  | {
      type: "approval_required";
      tool_call_id: string;
      tool: string;
      sql: string | null;
    }
  | { type: "propose_edit"; sql: string; explanation: string; scoped: boolean }
  | {
      type: "done";
      message_id: string;
      usage: { input: number; output: number };
    }
  | { type: "error"; message: string };

// A pending write awaiting the user's approve/deny decision.
export interface PendingApproval {
  tool_call_id: string;
  tool: string;
  sql: string | null;
}
