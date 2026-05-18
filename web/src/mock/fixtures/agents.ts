import type { Agent } from "@/types/agent";

export const AGENTS: Agent[] = [
  {
    id: "ag-1",
    name: "agent-a",
    status: "healthy",
    capabilities: {
      duckdb_version: "1.5.2",
      extensions: ["unity_catalog", "delta", "httpfs", "azure"],
      memory_limit_gb: 6,
      cores: 4,
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
      extensions: ["unity_catalog", "delta", "httpfs"],
      memory_limit_gb: 12,
      cores: 8,
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
      extensions: ["unity_catalog", "delta", "httpfs", "azure"],
      memory_limit_gb: 6,
      cores: 2,
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
      extensions: ["unity_catalog", "delta"],
      memory_limit_gb: 6,
      cores: 2,
      tailscale_ip: "100.74.12.30",
      host: "sandbox",
    },
    last_ping_at: new Date(Date.now() - 12000).toISOString(),
    created_at: "2026-03-01T00:00:00Z",
  },
];
