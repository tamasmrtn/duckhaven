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

export type AgentLifecycle =
  "provisioning" | "running" | "terminating" | "terminated" | "failed";

export interface Agent {
  id: string;
  name: string;
  status: AgentStatus;
  // Null until the agent dials home and advertises itself (e.g. an elastic agent
  // still provisioning).
  capabilities: AgentCapabilities | null;
  last_ping_at: string | null;
  created_at: string;
  // Elastic-agent fields; null for a static, operator-run agent.
  provider?: string | null;
  lifecycle?: AgentLifecycle | null;
  requested_cpu?: number | null;
  requested_memory_gb?: number | null;
  hourly_cost?: number | null;
  idle_timeout_minutes?: number | null;
}

export interface ComputeOptions {
  enabled: boolean;
  provider: string;
  // null when the configured provider prices nothing — render no cost.
  currency: string | null;
  cpu_min: number;
  cpu_max: number;
  cpu_step: number;
  memory_min_gb: number;
  memory_max_gb: number;
  memory_step_gb: number;
  price_vcpu_hour: number;
  price_memory_gb_hour: number;
  default_idle_minutes: number;
}

export interface CreateElasticAgentBody {
  cpu: number;
  memory_gb: number;
  idle_timeout_minutes?: number;
  name?: string;
}

export interface BootstrapToken {
  token: string;
  expires_at: string;
  control_plane_url: string;
  agent_image: string;
}

export function agentSupportsBackend(agent: Agent, kind: BackendKind): boolean {
  // A not-yet-registered agent (no advertised capabilities) supports nothing.
  if (!agent.capabilities) return false;
  const { extensions } = agent.capabilities;
  // Every backend is object storage: object_store is backed by the bundled
  // MinIO (S3) and needs httpfs, just like s3.
  if (kind === "adls_gen2") return extensions.includes("azure");
  return extensions.includes("httpfs");
}
