import type { Agent } from "@/types/agent";

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
  ];
}

export let AGENTS = makeAgents();

export function resetAgents(): void {
  AGENTS = makeAgents();
}
