import type { BackendKind } from "./storage-backend";

export type AgentStatus = "healthy" | "unavailable" | "degraded";

export interface AgentCapabilities {
  duckdb_version: string;
  extensions: string[];
  memory_limit_gb: number;
  cores: number;
  tailscale_ip: string | null;
  host: string | null;
}

export interface Agent {
  id: string;
  name: string;
  status: AgentStatus;
  capabilities: AgentCapabilities;
  last_ping_at: string | null;
  created_at: string;
}

export function agentSupportsBackend(agent: Agent, kind: BackendKind): boolean {
  const { extensions } = agent.capabilities;
  if (kind === "s3") return extensions.includes("httpfs");
  if (kind === "adls_gen2") return extensions.includes("azure");
  return true;
}
