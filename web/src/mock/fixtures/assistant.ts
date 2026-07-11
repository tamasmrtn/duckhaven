import type {
  AssistantToolCall,
  Conversation,
  TranscriptItem,
} from "@/types/assistant";

export interface MockConversation extends Conversation {
  transcript: TranscriptItem[];
  tool_calls: AssistantToolCall[];
  history_truncated: boolean;
}

function seed(): MockConversation[] {
  return [
    {
      id: "conv-1",
      workspace_id: "ws-1",
      title: "Exploring events",
      total_input_tokens: 120,
      total_output_tokens: 60,
      created_at: "2026-07-01T10:00:00Z",
      updated_at: "2026-07-01T10:05:00Z",
      history_truncated: false,
      transcript: [
        { role: "user", text: "How many events are there?", sql: null },
        {
          role: "assistant",
          text: "There are 42 events in the events table.",
          sql: "SELECT count(*) FROM events",
        },
      ],
      tool_calls: [
        {
          id: "tc-1",
          tool: "run_sql",
          args: { sql: "SELECT count(*) FROM events" },
          status: "ok",
          detail: null,
          query_id: "q-1",
          latency_ms: 85,
          created_at: "2026-07-01T10:04:30Z",
        },
      ],
    },
    {
      id: "conv-2",
      workspace_id: "ws-1",
      title: "Revenue check",
      total_input_tokens: 40,
      total_output_tokens: 20,
      created_at: "2026-07-02T09:00:00Z",
      updated_at: "2026-07-02T09:02:00Z",
      history_truncated: false,
      transcript: [],
      tool_calls: [],
    },
  ];
}

export let CONVERSATIONS: MockConversation[] = seed();

// Whether the mock assistant is "enabled" (mirrors ASSISTANT_ENABLED). Tests flip
// this to exercise the disabled UI state.
export let ASSISTANT_ENABLED = true;

export function setAssistantEnabled(value: boolean): void {
  ASSISTANT_ENABLED = value;
}

export function resetAssistant(): void {
  CONVERSATIONS = seed();
  ASSISTANT_ENABLED = true;
}
