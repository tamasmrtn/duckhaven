import { describe, it, expect } from "vitest";
import { resolveWorksheetAgent } from "@/features/worksheet/state/resolveAgent";
import type { Agent } from "@/types/agent";

function agent(id: string, over: Partial<Agent> = {}): Agent {
  return {
    id,
    name: id,
    status: "healthy",
    capabilities: {
      duckdb_version: "1.5.5",
      extensions: ["httpfs", "iceberg", "ducklake", "postgres_scanner"],
      memory_limit_gb: 12,
      cores: 6,
      cpu_model: null,
      cpu_cores_physical: null,
      tailscale_ip: null,
      host: `${id}-host`,
    },
    last_ping_at: null,
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

const offline = (id: string) => agent(id, { status: "unavailable" });
const stoppedElastic = (id: string) =>
  agent(id, {
    status: "unavailable",
    provider: "null",
    lifecycle: "terminated",
    capabilities: null,
  });
const noDuckLake = (id: string) =>
  agent(id, {
    capabilities: { ...agent(id).capabilities!, extensions: ["httpfs"] },
  });

const base = { worksheetAgentId: null, lastUsedAgentId: null };

describe("resolveWorksheetAgent", () => {
  it("never picks an offline agent just because it is listed first", () => {
    // The reported bug: a warm cache made agents[0] the default, and agents[0]
    // was an offline static agent, so Run failed with "Agent not connected".
    const result = resolveWorksheetAgent({
      ...base,
      agents: [offline("test-agent-1"), agent("ducklake-bench")],
    });
    expect(result).toMatchObject({ agentId: "ducklake-bench", source: "healthy" });
  });

  it("keeps the worksheet's own agent while it is usable", () => {
    const result = resolveWorksheetAgent({
      ...base,
      worksheetAgentId: "b",
      lastUsedAgentId: "a",
      agents: [agent("a"), agent("b")],
    });
    expect(result).toMatchObject({ agentId: "b", source: "worksheet" });
  });

  it("skips a worksheet agent that went offline or cannot serve the catalogs", () => {
    expect(
      resolveWorksheetAgent({
        ...base,
        worksheetAgentId: "down",
        agents: [offline("down"), agent("up")],
      }),
    ).toMatchObject({ agentId: "up" });
    expect(
      resolveWorksheetAgent({
        ...base,
        worksheetAgentId: "plain",
        catalogKinds: ["ducklake"],
        agents: [noDuckLake("plain"), agent("lake")],
      }),
    ).toMatchObject({ agentId: "lake" });
  });

  it("prefers the last-used agent over the first healthy one", () => {
    const result = resolveWorksheetAgent({
      ...base,
      lastUsedAgentId: "b",
      agents: [agent("a"), agent("b")],
    });
    expect(result).toMatchObject({ agentId: "b", source: "last-used" });
  });

  it("prefers a healthy agent over a degraded one", () => {
    const result = resolveWorksheetAgent({
      ...base,
      agents: [agent("slow", { status: "degraded" }), agent("fine")],
    });
    expect(result).toMatchObject({ agentId: "fine" });
  });

  it("falls back to a degraded agent", () => {
    const result = resolveWorksheetAgent({
      ...base,
      agents: [offline("down"), agent("slow", { status: "degraded" })],
    });
    expect(result).toMatchObject({ agentId: "slow" });
  });

  it("starts a stopped elastic agent when nothing is running", () => {
    const result = resolveWorksheetAgent({
      ...base,
      agents: [offline("static"), stoppedElastic("warehouse")],
    });
    expect(result).toEqual({
      agentId: "warehouse",
      source: "elastic-restart",
      willStart: true,
    });
  });

  it("does not pick an elastic agent that is merely disconnected", () => {
    // The API only restarts a terminated or failed instance; a running one
    // that lost its socket would just 503.
    const result = resolveWorksheetAgent({
      ...base,
      agents: [
        agent("gone", { status: "unavailable", provider: "null", lifecycle: "running" }),
      ],
    });
    expect(result).toEqual({ agentId: null, reason: "none-compatible" });
  });

  it("says why nothing was picked", () => {
    expect(resolveWorksheetAgent({ ...base, agents: [] })).toEqual({
      agentId: null,
      reason: "no-agents",
    });
    expect(
      resolveWorksheetAgent({
        ...base,
        catalogKinds: ["ducklake"],
        agents: [noDuckLake("a"), offline("b")],
      }),
    ).toEqual({ agentId: null, reason: "none-compatible" });
  });

  it("checks the storage backends as well as the catalog kinds", () => {
    const result = resolveWorksheetAgent({
      ...base,
      backends: ["adls_gen2"],
      agents: [agent("s3-only"), agent("azure", {
        capabilities: { ...agent("azure").capabilities!, extensions: ["httpfs", "azure"] },
      })],
    });
    expect(result).toMatchObject({ agentId: "azure" });
  });
});
