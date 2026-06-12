import type { BackendKind } from "./storage-backend";

export type AgentStatus = "healthy" | "unavailable" | "degraded";

export interface AgentCapabilities {
  duckdb_version: string;
  extensions: string[];
  memory_limit_gb: number;
  cores: number;
  cpu_model: string | null;
  cpu_cores_physical: number | null;
  tailscale_ip: string | null;
  host: string | null;
}

export interface MetricsSample {
  cpu_percent: number;
  memory_percent: number;
  running_queries: number;
  queued_queries: number;
  active_profile: string;
  sampled_at: string;
}

export interface AgentMetrics {
  agent_id: string;
  name: string;
  samples: MetricsSample[];
}

export interface Agent {
  id: string;
  name: string;
  status: AgentStatus;
  capabilities: AgentCapabilities;
  last_ping_at: string | null;
  created_at: string;
}

export interface BootstrapToken {
  token: string;
  expires_at: string;
  control_plane_url: string;
  agent_image: string;
}

export function agentSupportsBackend(agent: Agent, kind: BackendKind): boolean {
  const { extensions } = agent.capabilities;
  // Every backend is object storage: object_store is backed by the bundled
  // MinIO (S3) and needs httpfs, just like s3.
  if (kind === "adls_gen2") return extensions.includes("azure");
  return extensions.includes("httpfs");
}
