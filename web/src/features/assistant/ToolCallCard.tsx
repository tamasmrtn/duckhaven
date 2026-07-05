import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import type { AssistantToolCall } from "@/types/assistant";

const STATUS_VARIANT: Record<
  string,
  "default" | "secondary" | "destructive" | "outline"
> = {
  ok: "secondary",
  denied: "outline",
  approval_required: "outline",
  error: "destructive",
};

/** One row of the assistant's tool-call audit trail. */
export function ToolCallCard({
  ws,
  call,
}: {
  ws: string;
  call: AssistantToolCall;
}) {
  const sql =
    call.args && typeof call.args["sql"] === "string"
      ? (call.args["sql"] as string)
      : null;
  return (
    <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        <span className="font-mono text-text-primary">{call.tool}</span>
        <Badge variant={STATUS_VARIANT[call.status] ?? "outline"}>
          {call.status}
        </Badge>
        {call.latency_ms != null && (
          <span className="text-text-secondary">{call.latency_ms}ms</span>
        )}
        {call.query_id && (
          <Link
            to="/$ws/queries/$queryId"
            params={{ ws, queryId: call.query_id }}
            className="ml-auto text-[var(--brand-slate-blue)] hover:underline"
          >
            View result
          </Link>
        )}
      </div>
      {sql && (
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-[11px] text-text-secondary">
          {sql}
        </pre>
      )}
      {call.detail && <p className="mt-1 text-text-secondary">{call.detail}</p>}
    </div>
  );
}
