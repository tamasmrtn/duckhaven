import type { Agent, AgentStatus } from "@/types/agent";

// The currency comes from the configured provider (its own pricing page quotes
// the rates an operator copies). It is deliberately required rather than defaulted:
// a provider that prices nothing returns null, and the caller must then render no
// cost at all instead of putting a cloud symbol on hardware you already own.
export function formatCost(cost: number, currency: string): string {
  const symbol = currency === "USD" ? "$" : `${currency} `;
  return `${symbol}${cost.toFixed(2)}/hr`;
}

export const statusDot: Record<AgentStatus, string> = {
  healthy: "bg-[var(--status-success)]",
  degraded: "bg-[var(--status-running)]",
  unavailable: "bg-[var(--status-failed)]",
};

// A provisioning/terminating elastic agent isn't "down" — it's in transition, so
// it shows amber rather than the red of a genuinely unavailable agent.
export function agentDotClass(agent: Agent): string {
  if (agent.lifecycle === "provisioning" || agent.lifecycle === "terminating") {
    return "bg-[var(--status-running)]";
  }
  return statusDot[agent.status];
}

export function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 5) return "just now";
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}
