import type { AgentMetrics } from "@/types/agent";

// A rolling ~1 minute window (30 samples at 2s) of synthetic utilization for the
// two healthy agents, so the live charts have multiple series to draw.
function makeSamples(seed: number) {
  const now = Date.now();
  return Array.from({ length: 30 }, (_, i) => {
    const t = now - (29 - i) * 2000;
    const wave = Math.sin((i + seed) / 4) * 20;
    return {
      cpu_percent: Math.round(Math.max(2, Math.min(98, 45 + wave + seed * 5))),
      memory_percent: Math.round(
        Math.max(5, Math.min(95, 50 + wave / 2 + seed * 3)),
      ),
      running_queries: Math.max(0, Math.round(1 + Math.sin((i + seed) / 5))),
      queued_queries: seed > 2 ? Math.max(0, Math.round(Math.sin(i / 6))) : 0,
      active_profile: "decaying_3",
      sampled_at: new Date(t).toISOString(),
    };
  });
}

function makeMetrics(): AgentMetrics[] {
  return [
    { agent_id: "ag-1", name: "agent-a", samples: makeSamples(1) },
    { agent_id: "ag-2", name: "agent-b", samples: makeSamples(3) },
  ];
}

export let METRICS = makeMetrics();

export function resetMetrics(): void {
  METRICS = makeMetrics();
}
