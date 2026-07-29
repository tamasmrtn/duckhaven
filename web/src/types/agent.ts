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

/**
 * What the current user may do with an agent: `use` targets it for queries,
 * sessions and schedules and reads its monitoring; `operate` adds restart,
 * terminate and disconnect; `admin` adds delete and managing its access.
 */
export type AgentTier = "use" | "operate" | "admin";

/**
 * `open` — any authenticated user may target the agent (the default, and how
 * every agent behaved before per-agent access existed).
 * `restricted` — using it requires an explicit grant.
 */
export type AgentAccessMode = "open" | "restricted";

const TIER_ORDER: Record<AgentTier, number> = {
  use: 0,
  operate: 1,
  admin: 2,
};

/**
 * Whether the caller's tier on `agent` reaches `need`.
 *
 * The tier itself is resolved by the server and shipped on every agent, so this
 * is only an ordering comparison — the UI never re-derives who has access.
 */
export function agentTierAtLeast(agent: Agent, need: AgentTier): boolean {
  if (!agent.access_tier) return false;
  return TIER_ORDER[agent.access_tier] >= TIER_ORDER[need];
}

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
  // The requesting user's tier on this agent, resolved per request. An agent the
  // caller has no tier on is never returned, so in practice this is always set.
  access_tier?: AgentTier | null;
  access_mode?: AgentAccessMode;
}

/** One principal's tier on one agent. Exactly one of the id pairs is set. */
export interface AgentGrant {
  id: string;
  user_id: string | null;
  user_name: string | null;
  workspace_id: string | null;
  workspace_name: string | null;
  tier: AgentTier;
  created_at: string;
}

/** A candidate grantee offered by the Access tab's picker. */
export interface AgentGrantPrincipal {
  kind: "user" | "workspace";
  id: string;
  name: string;
  email: string | null;
  is_service_account: boolean;
}

/** Everything the Access tab renders, in one response. */
export interface AgentAccess {
  agent_id: string;
  access_mode: AgentAccessMode;
  grants: AgentGrant[];
  principals: AgentGrantPrincipal[];
}

export interface AgentGrantUpsert {
  user_id?: string;
  workspace_id?: string;
  tier: AgentTier;
}

/** The look-back windows the monitoring page offers, shortest first. */
export const MONITORING_WINDOWS = ["1h", "3h", "8h", "12h", "24h"] as const;
export type MonitoringWindow = (typeof MONITORING_WINDOWS)[number];

/**
 * What the agent was doing during one bucket.
 *
 * `unknown` is not `down`: it means no lifecycle trail covers that bucket (an
 * agent older than the trail), where claiming downtime would invent an outage.
 */
export type ActivityState =
  "down" | "starting" | "query" | "other" | "ready" | "unknown";

export interface PeakQueryPoint {
  t: string;
  running: number;
  queued: number;
}

export interface CompletedQueryPoint {
  t: string;
  per_minute: number;
}

export interface ActivityPoint {
  t: string;
  state: ActivityState;
}

export interface FailurePoint {
  t: string;
  reason: string;
  count: number;
}

export interface UtilizationPoint {
  t: string;
  // All null for a bucket the agent reported nothing in, so the chart draws a
  // gap rather than a line through a zero it never measured.
  cpu_avg: number | null;
  cpu_max: number | null;
  mem_avg: number | null;
  mem_max: number | null;
}

export interface MonitoringSummary {
  uptime_s: number;
  // Share of connected time with query activity; null when never connected.
  busy_ratio: number | null;
  completed: number;
  failed: number;
  idle_timeout_minutes: number | null;
}

/** Every series for one agent over one window, on a shared bucket grid. */
export interface AgentMonitoring {
  window: MonitoringWindow;
  bucket_seconds: number;
  start: string;
  end: string;
  peak_query_count: PeakQueryPoint[];
  completed_query_count: CompletedQueryPoint[];
  activity: ActivityPoint[];
  failures: FailurePoint[];
  utilization: UtilizationPoint[];
  summary: MonitoringSummary;
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
