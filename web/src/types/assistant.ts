export interface AssistantStatus {
  enabled: boolean;
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

export interface TranscriptItem {
  role: "user" | "assistant";
  text: string;
  // The SQL this turn ran or proposed, if any (attributed server-side).
  sql: string | null;
}

export interface AssistantToolCall {
  id: string;
  tool: string;
  args: Record<string, unknown> | null;
  status: string;
  detail: string | null;
  query_id: string | null;
  latency_ms: number | null;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  transcript: TranscriptItem[];
  tool_calls: AssistantToolCall[];
}

// Server-sent events streamed from a turn.
export type AssistantFrame =
  | { type: "token"; text: string }
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
