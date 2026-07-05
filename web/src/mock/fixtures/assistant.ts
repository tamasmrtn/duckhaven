import type {
  AssistantToolCall,
  Conversation,
  TranscriptItem,
} from "@/types/assistant";

export interface MockConversation extends Conversation {
  transcript: TranscriptItem[];
  tool_calls: AssistantToolCall[];
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
      transcript: [
        { role: "user", text: "How many events are there?" },
        { role: "assistant", text: "There are 42 events in the events table." },
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
  ];
}

export let CONVERSATIONS: MockConversation[] = seed();

export function resetAssistant(): void {
  CONVERSATIONS = seed();
}
