import type {
  Agent,
  AgentGrant,
  AgentGrantPrincipal,
  AgentRuntime,
  Runtime,
} from "@/types/agent";

/** The curated runtimes the mock control plane knows: 1.5 (default) and a 2.0 beta. */
export const RUNTIMES: Runtime[] = [
  {
    id: "1.5",
    display_name: "DuckDB 1.5",
    duckdb_line: "1.5",
    status: "ga",
    extensions: ["httpfs", "azure", "iceberg", "ducklake", "postgres"],
    ducklake_format: "1.0",
    upstream_eol: "2026-11-01",
    default: true,
  },
  {
    id: "2.0",
    display_name: "DuckDB 2.0",
    duckdb_line: "2.0",
    status: "beta",
    extensions: ["httpfs", "azure", "iceberg", "ducklake", "postgres"],
    ducklake_format: "1.0",
    upstream_eol: null,
    default: false,
  },
];

/** How the mock control plane judges a runtime id, as the API's resolve() does. */
export function agentRuntime(runtimeId: string | null): AgentRuntime {
  const runtime = RUNTIMES.find((r) => r.id === runtimeId);
  return runtime
    ? {
        id: runtime.id,
        display_name: runtime.display_name,
        status: runtime.status,
        state: "ok",
        default: runtime.default,
      }
    : {
        id: runtimeId,
        display_name: null,
        status: null,
        state: "unrecognized",
        default: false,
      };
}

// last_ping_at uses relative offsets so the "Ns ago" rendering stays stable
// regardless of absolute clock. DELETE /credential mutates status, so the array
// is rebuildable via resetAgents() for test isolation.
function makeAgents(): Agent[] {
  return [
    {
      id: "ag-1",
      name: "agent-a",
      status: "healthy",
      capabilities: {
        duckdb_version: "1.5.2",
        extensions: ["iceberg", "httpfs", "azure"],
        memory_limit_gb: 6,
        cores: 4,
        cpu_model: "AMD Ryzen 7 5800X",
        cpu_cores_physical: 4,
        tailscale_ip: "100.74.12.10",
        host: "homeserver-01",
      },
      last_ping_at: new Date(Date.now() - 2000).toISOString(),
      created_at: "2026-01-01T00:00:00Z",
    },
    {
      id: "ag-2",
      name: "agent-b",
      status: "healthy",
      capabilities: {
        duckdb_version: "1.5.2",
        extensions: ["iceberg", "httpfs"],
        memory_limit_gb: 12,
        cores: 8,
        cpu_model: "Intel Xeon E5-2680 v4",
        cpu_cores_physical: 8,
        tailscale_ip: "100.74.12.20",
        host: "beefy-vm",
      },
      last_ping_at: new Date(Date.now() - 1000).toISOString(),
      created_at: "2026-01-05T00:00:00Z",
    },
    {
      id: "ag-3",
      name: "agent-c",
      status: "unavailable",
      capabilities: {
        duckdb_version: "1.4.3",
        extensions: ["iceberg", "httpfs", "azure"],
        memory_limit_gb: 6,
        cores: 2,
        cpu_model: null,
        cpu_cores_physical: null,
        tailscale_ip: null,
        host: null,
      },
      last_ping_at: new Date(Date.now() - 240000).toISOString(),
      created_at: "2026-02-01T00:00:00Z",
    },
    {
      id: "ag-4",
      name: "agent-d",
      status: "degraded",
      capabilities: {
        duckdb_version: "1.5.2",
        extensions: ["iceberg"],
        memory_limit_gb: 6,
        cores: 2,
        cpu_model: "Apple M2",
        cpu_cores_physical: 2,
        tailscale_ip: "100.74.12.30",
        host: "sandbox",
      },
      last_ping_at: new Date(Date.now() - 12000).toISOString(),
      created_at: "2026-03-01T00:00:00Z",
    },
    {
      id: "ag-5",
      name: "warehouse-a",
      status: "healthy",
      capabilities: {
        duckdb_version: "1.5.4",
        extensions: ["iceberg", "httpfs", "azure"],
        memory_limit_gb: 16,
        cores: 4,
        cpu_model: "Azure Container Instances",
        cpu_cores_physical: 4,
        tailscale_ip: null,
        host: "aci",
      },
      last_ping_at: new Date(Date.now() - 3000).toISOString(),
      created_at: "2026-06-01T00:00:00Z",
      provider: "azure_aci",
      lifecycle: "running",
      requested_cpu: 4,
      requested_memory_gb: 16,
      hourly_cost: 0.2808,
    },
  ];
}

// The mock signs in as a full admin (`agents:manage`), which resolves to the top
// tier on every agent — so the dev app exercises the unrestricted view. Tests
// override `access_tier` per agent to render the narrower ones. Each agent's
// runtime is its DuckDB line, the way the API infers it for an older image: the
// 1.4 agent is on a line no curated runtime covers.
function withAccess(agents: Agent[]): Agent[] {
  return agents.map((a) => {
    const line = a.capabilities?.duckdb_version
      .split(".")
      .slice(0, 2)
      .join(".");
    return {
      ...a,
      access_tier: "admin",
      access_mode: "open",
      runtime: line ? agentRuntime(line) : null,
    };
  });
}

export let AGENTS: Agent[] = withAccess(makeAgents());

export function resetAgents(): void {
  AGENTS = withAccess(makeAgents());
  AGENT_GRANTS = {};
}

/** Grants per agent id, mutated by the Access tab's handlers. */
export let AGENT_GRANTS: Record<string, AgentGrant[]> = {};

/** Candidate grantees the Access tab's picker offers. */
export const AGENT_GRANT_PRINCIPALS: AgentGrantPrincipal[] = [
  {
    kind: "user",
    id: "u-1",
    name: "Ada Lovelace",
    email: "ada@duckhaven.dev",
    is_service_account: false,
  },
  {
    kind: "user",
    id: "u-2",
    name: "dbt-runner",
    email: "dbt@duckhaven.dev",
    is_service_account: true,
  },
  {
    kind: "workspace",
    id: "ws-1",
    name: "Acme Analytics",
    email: null,
    is_service_account: false,
  },
];
