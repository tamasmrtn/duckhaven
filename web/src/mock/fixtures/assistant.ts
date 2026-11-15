import type {
  AssistantToolCall,
  ChatChart,
  ChatProposal,
  Conversation,
  TranscriptItem,
} from "@/types/assistant";
import { SALES_RUN } from "./dashboards";

const BY_COUNTRY = SALES_RUN.find((w) => w.id === "by_country")!;

/** Changes the assistant proposes to the sales dashboard: the mock's proposal-1. */
export const CHAT_PROPOSAL: ChatProposal = {
  type: "dashboard_proposal",
  tool_call_id: "call-propose",
  proposal: {
    id: "proposal-1",
    dashboard: "sales-overview",
    title: "Sales, weekly",
    summary: "Retitle the dashboard for its weekly review.",
    changes: [{ path: "title", change: "changed" }],
    managed: false,
    expires_at: "2026-11-16T09:00:00Z",
  },
};

/** A bar chart the assistant draws from a query it ran: revenue by country. */
export const CHAT_CHART: ChatChart = {
  type: "chart",
  tool_call_id: "call-chart",
  widget: {
    id: "by_country",
    type: "bar",
    title: "Revenue by country",
    sql: "SELECT country, SUM(total_amount) AS revenue\nFROM warehouse.analytics.orders\nGROUP BY 1\nORDER BY 2 DESC",
  },
  event: { ...BY_COUNTRY, rows: { ...BY_COUNTRY.rows!, query_id: "q-chart" } },
  result_hash: "hash-by-country",
};

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
        {
          role: "user",
          text: "How many events are there?",
          sql: null,
          sources: null,
        },
        {
          role: "assistant",
          text: "There are 42 events in the events table.",
          sql: "SELECT count(*) FROM events",
          sources: null,
        },
        {
          role: "user",
          text: "How do I query it as it was last week?",
          sql: null,
          sources: null,
        },
        {
          // Carries sources so the Sources row is visible in mock-driven dev
          // without needing a live model.
          role: "assistant",
          text: "Use DuckDB's AT clause: `SELECT * FROM events AT (TIMESTAMP => '2026-06-24')`.",
          sql: null,
          sources: [
            {
              path: "data/snapshots-time-travel.md",
              title: "Snapshots & time travel",
              url: "https://tamasmrtn.github.io/duckhaven/data/snapshots-time-travel/",
            },
            {
              path: "reference/sql-support.md",
              title: "SQL support",
              url: "https://tamasmrtn.github.io/duckhaven/reference/sql-support/",
            },
          ],
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
          tables: [
            { catalog: "acme_analytics", schema_name: "raw", table: "events" },
          ],
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

// Assistant availability per workspace slug — the real endpoint answers per
// workspace, so a single global flag could not cover a user moving between a
// granted and a non-granted one. Anything not listed is "ok".
export let ASSISTANT_AVAILABILITY: Record<string, string> = {};

export function setAssistantEnabled(value: boolean): void {
  ASSISTANT_ENABLED = value;
}

export function setAssistantAvailability(
  availability: Record<string, string>,
): void {
  ASSISTANT_AVAILABILITY = availability;
}

export function resetAssistant(): void {
  CONVERSATIONS = seed();
  ASSISTANT_ENABLED = true;
  ASSISTANT_AVAILABILITY = {};
}
