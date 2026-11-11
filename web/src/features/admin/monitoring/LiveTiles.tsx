import { useAgentMetrics } from "@/queries/metrics";
import type { Agent, MetricsSample } from "@/types/agent";
import { cn } from "@/utils";

/** The agent's size: what CPU% and memory% are percentages *of*. */
export function agentSize(agent: Agent): {
  cores: number | null;
  memoryGb: number | null;
} {
  return {
    cores: agent.requested_cpu ?? agent.capabilities?.cores ?? null,
    memoryGb:
      agent.requested_memory_gb ?? agent.capabilities?.memory_limit_gb ?? null,
  };
}

function Tile({
  label,
  value,
  detail,
  tone,
  word,
}: {
  label: string;
  value: string;
  detail?: string;
  tone?: string;
  // A status word ("terminated") rather than a measurement: set smaller so a
  // state does not shout at the size of the numbers beside it.
  word?: boolean;
}) {
  return (
    <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-4 py-3">
      <p className="text-2xs uppercase tracking-wide text-text-tertiary">
        {label}
      </p>
      <p
        className={cn(
          // One line box for both sizes, so the detail line under every tile
          // sits at the same height whatever the value's type size.
          "mt-1 font-mono font-tabular leading-8",
          word ? "text-md capitalize" : "text-xl",
          tone,
        )}
        data-testid={`live-${label.toLowerCase().replace(/\s+/g, "-")}`}
      >
        {value}
      </p>
      <p className="mt-0.5 min-h-4 text-2xs text-text-tertiary">
        {detail ?? ""}
      </p>
    </div>
  );
}

function ago(iso: string): string {
  const s = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 1000));
  return s < 60 ? `${s}s ago` : `${Math.round(s / 60)}m ago`;
}

const DASH = "—";

/**
 * What the agent is doing right now, from its own 2-second samples.
 *
 * "Executing" counts statements actually running. An open dbt/BI connection with
 * nothing running holds an admission slot but is idle, so it is shown beside the
 * count, never in it. A value an older agent cannot measure reads "—", not 0.
 */
export function LiveTiles({ agent }: { agent: Agent }) {
  const { data: metrics = [] } = useAgentMetrics();
  const samples = metrics.find((m) => m.agent_id === agent.id)?.samples ?? [];
  const latest: MetricsSample | undefined = samples[samples.length - 1];
  const { cores, memoryGb } = agentSize(agent);
  const idle = latest?.idle_sessions ?? 0;
  const parked = latest?.growth_waiting ?? 0;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      <Tile
        label="Status"
        value={
          agent.lifecycle && agent.lifecycle !== "running"
            ? agent.lifecycle
            : agent.status
        }
        detail={
          latest ? `sampled ${ago(latest.sampled_at)}` : "no live samples"
        }
        word
        tone={
          agent.status === "healthy"
            ? "text-[var(--status-success)]"
            : // A stopped agent reports "unavailable" like a crashed one, but
              // finishing is not a fault; only `failed` and a down agent are red.
              agent.lifecycle === "terminated"
              ? "text-text-secondary"
              : "text-[var(--status-failed)]"
        }
      />
      <Tile
        label="Executing"
        value={
          latest?.executing_queries != null
            ? `${latest.executing_queries}`
            : DASH
        }
        detail={
          idle
            ? `+${idle} idle connection${idle === 1 ? "" : "s"}`
            : "queries running now"
        }
      />
      <Tile
        label="Queued"
        value={latest ? `${latest.queued_queries}` : DASH}
        detail={parked ? `+${parked} waiting for memory` : "waiting for a slot"}
      />
      <Tile
        label="CPU"
        value={latest ? `${Math.round(latest.cpu_percent)}%` : DASH}
        detail={cores ? `of ${cores} vCPU` : undefined}
      />
      <Tile
        label="Memory"
        value={latest ? `${Math.round(latest.memory_percent)}%` : DASH}
        detail={memoryGb ? `of ${memoryGb} GB` : undefined}
      />
    </div>
  );
}
