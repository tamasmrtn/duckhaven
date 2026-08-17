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

  // For a metric query the interesting thing is not the arguments but *which
  // agreed definitions produced the number*. Surfacing them here is what makes
  // an answer checkable without opening a trace.
  const model =
    call.tool === "query_metric" || call.tool === "explain_metric"
      ? ((call.args?.["model"] as string | undefined) ?? null)
      : null;
  const metrics = Array.isArray(call.args?.["metrics"])
    ? (call.args["metrics"] as string[])
    : call.tool === "explain_metric" &&
        typeof call.args?.["metric"] === "string"
      ? [call.args["metric"] as string]
      : [];

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
      {model && (
        <p className="mt-1 text-text-secondary">
          Using{" "}
          {metrics.length > 0 && (
            <>
              <span className="font-mono">{metrics.join(", ")}</span> from{" "}
            </>
          )}
          <Link
            to="/$ws/semantic/$model"
            params={{ ws, model }}
            className="text-[var(--brand-slate-blue)] hover:underline"
          >
            {model}
          </Link>
        </p>
      )}
      {sql && (
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-[11px] text-text-secondary">
          {sql}
        </pre>
      )}
      {call.detail && <p className="mt-1 text-text-secondary">{call.detail}</p>}
    </div>
  );
}
