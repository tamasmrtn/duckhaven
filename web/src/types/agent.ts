import type { QueryStatus } from "./query";
import type { CatalogKind } from "./catalog";
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
  protocol_features?: string[];
  // What the agent reports about its runtime; absent on images built before
  // runtimes existed.
  runtime_id?: string | null;
  engine_version?: string | null;
  agent_version?: string | null;
  platform?: string | null;
  // Whether DuckDB's configuration lock really applied; `disabled` is the
  // operator's choice, `failed` means SQL sessions are refused on this agent.
  sandbox?: "verified" | "failed" | "disabled" | null;
}

/**
 * A runtime's lifecycle: `beta` is only used when named, never picked
 * automatically; `deprecated` still runs but is closed to new compute; `retired`
 * is refused.
 */
export type RuntimeStatus = "beta" | "ga" | "deprecated" | "retired";

/** One curated agent runtime: a DuckDB line plus its baked extensions. */
export interface Runtime {
  id: string;
  display_name: string;
  duckdb_line: string;
  status: RuntimeStatus;
  extensions: string[];
  ducklake_format: string | null;
  upstream_eol: string | null;
  // Whether auto-provisioned compute runs it in this deployment.
  default: boolean;
}

/**
 * The control plane's view of an agent's runtime. `ok` and `inferred` (an image
 * from before runtimes, matched by its DuckDB line) are trusted with work;
 * `mismatch`, `unrecognized` and `retired` are refused; `pending` means the agent
 * has not reported yet.
 */
export type AgentRuntimeState =
  "pending" | "ok" | "inferred" | "mismatch" | "unrecognized" | "retired";

export interface AgentRuntime {
  id: string | null;
  display_name: string | null;
  status: RuntimeStatus | null;
  state: AgentRuntimeState;
  // The deployment's default runtime, which the server prefers when it picks.
  default?: boolean;
}

export interface MetricsSample {
  cpu_percent: number;
  memory_percent: number;
  // Every admission slot in use, including idle held SQL sessions.
  running_queries: number;
  queued_queries: number;
  active_profile: string;
  sampled_at: string;
  session_count?: number;
  growth_waiting?: number;
  // Measured only by newer agents; null/absent means "not measured", never 0.
  memory_peak_percent?: number | null;
  // Statements actually executing: an idle held session is in idle_sessions.
  executing_queries?: number | null;
  idle_sessions?: number | null;
  oom_kills?: number | null;
  interval_s?: number | null;
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
  runtime?: AgentRuntime | null;
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
export const MONITORING_WINDOWS = [
  "1h",
  "3h",
  "8h",
  "12h",
  "24h",
  "3d",
  "7d",
] as const;
export type MonitoringWindow = (typeof MONITORING_WINDOWS)[number];

/** A preset ending now, or an explicit (zoomed) range. */
export type MonitoringRange =
  { window: MonitoringWindow } | { start: string; end: string };

/** Lifecycle state of a span of time; "unknown" is no record, not downtime. */
export type LifecycleState = "up" | "starting" | "down" | "unknown";

/** One bucket of every series. Flat, so every chart indexes the same row. */
export interface MonitoringBucket {
  t: string;
  // Shorter than bucket_seconds for a bucket the range cuts (e.g. the one in
  // progress now); `partial` says so.
  seconds: number;
  partial: boolean;
  // Where the agent's time went; these sum to `seconds`.
  busy_s: number;
  idle_s: number;
  starting_s: number;
  down_s: number;
  unknown_s: number;
  // Average number of queries in each state over the bucket (Little's law).
  running_avg: number;
  queued_avg: number;
  compute_wait_avg: number;
  // Most queries running at any single instant in the bucket.
  peak_running: number;
  done: number;
  cancelled: number;
  failed: Record<string, number>;
  wait_p95_ms: number | null;
  wait_n: number;
  // Sampled resources; null when the agent reported nothing (a gap, not 0).
  cpu_avg: number | null;
  cpu_max: number | null;
  mem_avg: number | null;
  mem_max: number | null;
  oom_kills: number | null;
  coverage: number | null;
}

export interface MonitoringSpan {
  start: string;
  end: string;
  state: LifecycleState;
}

export interface MonitoringSummary {
  uptime_s: number;
  busy_s: number;
  idle_s: number;
  // Share of up time with a query running; null when never up.
  busy_ratio: number | null;
  finished: number;
  failed: number;
  cancelled: number;
  failed_by_reason: Record<string, number>;
  wait_p95_ms: number | null;
  wait_n: number;
  peak_running: number;
  cpu_peak: number | null;
  mem_peak: number | null;
  resources_as_of: string | null;
}

/** Every series for one agent over one range, on a shared bucket grid. */
export interface AgentMonitoring {
  // The preset asked for; null for a custom (zoomed) range.
  preset: MonitoringWindow | null;
  range_start: string;
  range_end: string;
  bucket_seconds: number;
  generated_at: string;
  buckets: MonitoringBucket[];
  spans: MonitoringSpan[];
  summary: MonitoringSummary;
}

export const AGENT_QUERY_SORTS = [
  "started_at",
  "duration",
  "wait",
  "peak_memory",
  "cpu_time",
  "spill",
  "bytes_read",
] as const;
export type AgentQuerySort = (typeof AGENT_QUERY_SORTS)[number];

/** One run on an agent with what it cost — the Monitoring tab's table. */
export interface AgentQuery {
  id: string;
  workspace_id: string;
  user_name: string | null;
  sql: string;
  status: QueryStatus;
  origin: string | null;
  statement_type: string | null;
  started_at: string;
  running_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  wait_ms: number | null;
  row_count: number | null;
  error: string | null;
  failure_reason: string | null;
  peak_memory_bytes: number | null;
  cpu_time_ms: number | null;
  spill_bytes: number | null;
  bytes_read: number | null;
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
  // Runtimes new compute may use (never deprecated or retired ones), and the one
  // to preselect.
  runtimes?: Runtime[];
  default_runtime?: string | null;
}

export interface CreateElasticAgentBody {
  cpu: number;
  memory_gb: number;
  idle_timeout_minutes?: number;
  name?: string;
  // Chosen at creation so an agent meant to be reserved is never briefly usable by
  // everyone — it would otherwise register and start taking work before anyone
  // reached the Access tab. Omitted means `open`.
  access_mode?: AgentAccessMode;
  // Omitted means the deployment's default runtime. A beta runtime needs
  // `allow_beta`, so nobody lands on a pre-release DuckDB by accident.
  runtime_id?: string;
  allow_beta?: boolean;
}

export interface BootstrapToken {
  token: string;
  expires_at: string;
  control_plane_url: string;
  agent_image: string;
  runtime_id: string;
}

/** The runtime the control plane trusts this agent with, as a short label. */
export function runtimeLabel(agent: Agent): string | null {
  const name = agent.runtime?.display_name ?? null;
  const engine =
    agent.capabilities?.engine_version ?? agent.capabilities?.duckdb_version;
  if (name && engine) return `${name} · ${engine}`;
  return name ?? (engine ? `DuckDB ${engine}` : null);
}

/** Why work is refused on this agent's runtime, or null when it isn't. */
export function runtimeRefusal(agent: Agent): string | null {
  const runtime = agent.runtime;
  if (!runtime) return null;
  switch (runtime.state) {
    case "retired":
      return `Runtime ${runtime.display_name ?? runtime.id} is retired`;
    case "mismatch":
      return "Running a different runtime than it was created with";
    case "unrecognized":
      return "Not running a supported runtime";
    default:
      return null;
  }
}

/** Whether this agent runs a beta runtime, reached only by naming it. */
export function isBetaRuntime(agent: Agent): boolean {
  return agent.runtime?.status === "beta";
}

// Mirrors _CATALOG_KIND_EXTENSIONS in api/src/api/services/agent_capabilities.py.
const CATALOG_KIND_EXTENSIONS: Record<CatalogKind, readonly string[]> = {
  iceberg_polaris: [],
  ducklake: ["ducklake", "postgres_scanner"],
};

export function agentSupportsCatalogKind(
  agent: Agent,
  kind: CatalogKind,
): boolean {
  if (!agent.capabilities) return false;
  const { extensions } = agent.capabilities;
  return (CATALOG_KIND_EXTENSIONS[kind] ?? []).every((ext) =>
    extensions.includes(ext),
  );
}

/** The first extension this agent lacks for the kind, or null if it has them all. */
export function missingCatalogKindExtension(
  agent: Agent,
  kind: CatalogKind,
): string | null {
  const extensions = agent.capabilities?.extensions ?? [];
  return (
    (CATALOG_KIND_EXTENSIONS[kind] ?? []).find(
      (ext) => !extensions.includes(ext),
    ) ?? null
  );
}

export function agentSupportsBackend(agent: Agent, kind: BackendKind): boolean {
  // No advertised capabilities (not yet registered) supports nothing.
  if (!agent.capabilities) return false;
  const { extensions } = agent.capabilities;
  // Every backend is object storage; object_store is the bundled S3 store.
  if (kind === "adls_gen2") return extensions.includes("azure");
  return extensions.includes("httpfs");
}

/** What a workspace needs from an agent: every catalog kind and storage backend it attaches. */
export interface AgentRequirements {
  catalogKinds?: CatalogKind[];
  backends?: BackendKind[];
}

/**
 * How an agent can serve a workspace right now, for grouping the picker and
 * choosing a default:
 * - `running` — connected and compatible; a run goes straight to it.
 * - `startable` — a stopped elastic agent the API restarts for the run.
 * - `incompatible` — up, but missing an extension a catalog or backend needs.
 * - `unavailable` — offline and not something a run can start (a static agent,
 *   or an elastic one mid-teardown or still provisioning).
 */
export type AgentAvailability =
  | { kind: "running" }
  | { kind: "startable" }
  | { kind: "incompatible"; reason: string }
  | { kind: "unavailable" };

/**
 * Whether the API restarts this agent when a run targets it while it is down —
 * never on a retired runtime, which can't start again.
 */
export function agentRestartable(agent: Agent): boolean {
  return (
    !!agent.provider &&
    (agent.lifecycle === "terminated" || agent.lifecycle === "failed") &&
    agent.runtime?.status !== "retired"
  );
}

/** The first thing `agent` lacks to serve `needs`, or null if it lacks nothing. */
export function agentIncompatibility(
  agent: Agent,
  needs: AgentRequirements,
): string | null {
  for (const kind of needs.catalogKinds ?? []) {
    if (!agentSupportsCatalogKind(agent, kind)) {
      return `Missing extension for ${kind} catalogs: ${missingCatalogKindExtension(agent, kind)}`;
    }
  }
  for (const backend of needs.backends ?? []) {
    if (!agentSupportsBackend(agent, backend)) {
      return `Missing extension for ${backend === "adls_gen2" ? "azure" : "httpfs"}`;
    }
  }
  return null;
}

export function agentAvailability(
  agent: Agent,
  needs: AgentRequirements,
): AgentAvailability {
  // A stopped elastic agent advertises nothing until it dials home again, so
  // its capabilities cannot be checked here; the API checks them on dispatch.
  if (agent.status === "unavailable") {
    return agentRestartable(agent)
      ? { kind: "startable" }
      : { kind: "unavailable" };
  }
  if (agent.lifecycle === "terminating" || agent.lifecycle === "provisioning") {
    return { kind: "unavailable" };
  }
  const reason = runtimeRefusal(agent) ?? agentIncompatibility(agent, needs);
  return reason ? { kind: "incompatible", reason } : { kind: "running" };
}
