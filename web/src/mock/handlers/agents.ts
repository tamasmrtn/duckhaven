import { http, HttpResponse } from "msw";
import { AGENTS } from "../fixtures/agents";
import { nextBootstrapToken } from "../lib/seed";
import { httpError } from "../lib/errors";
import type { Agent } from "@/types/agent";

// Mirrors the backend's ACI ranges + rate defaults.
const PRICE_VCPU = 0.0486;
const PRICE_MEM = 0.0054;

let elasticSeq = 0;

export const agentHandlers = [
  http.get("/api/agents", () => {
    return HttpResponse.json(AGENTS);
  }),

  http.get("/api/admin/agents", () => {
    return HttpResponse.json(AGENTS);
  }),

  http.post("/api/admin/agents/bootstrap", () => {
    return HttpResponse.json({
      token: nextBootstrapToken(),
      expires_at: new Date(Date.now() + 86400000).toISOString(),
      control_plane_url: "ws://localhost:8000/agents/connect",
      agent_image: "ghcr.io/tamasmrtn/duckhaven-agent:latest",
    });
  }),

  http.get("/api/admin/agents/compute-options", () => {
    return HttpResponse.json({
      enabled: true,
      provider: "azure_aci",
      currency: "USD",
      cpu_min: 1,
      cpu_max: 4,
      cpu_step: 1,
      memory_min_gb: 1,
      memory_max_gb: 16,
      memory_step_gb: 1,
      price_vcpu_hour: PRICE_VCPU,
      price_memory_gb_hour: PRICE_MEM,
      default_idle_minutes: 15,
    });
  }),

  http.post("/api/admin/agents/elastic", async ({ request }) => {
    const body = (await request.json()) as {
      cpu: number;
      memory_gb: number;
      idle_timeout_minutes?: number;
      name?: string;
    };
    if (
      body.cpu < 1 ||
      body.cpu > 4 ||
      body.memory_gb < 1 ||
      body.memory_gb > 16
    ) {
      return httpError(422, "Invalid size");
    }
    elasticSeq += 1;
    const agent: Agent = {
      id: `ag-elastic-${elasticSeq}`,
      name: body.name || `elastic-${elasticSeq}`,
      status: "unavailable",
      capabilities: null,
      last_ping_at: null,
      created_at: new Date().toISOString(),
      provider: "azure_aci",
      lifecycle: "provisioning",
      requested_cpu: body.cpu,
      requested_memory_gb: body.memory_gb,
      hourly_cost:
        Math.round((body.cpu * PRICE_VCPU + body.memory_gb * PRICE_MEM) * 1e4) /
        1e4,
      idle_timeout_minutes: body.idle_timeout_minutes ?? null,
    };
    AGENTS.push(agent);
    return HttpResponse.json(agent, { status: 202 });
  }),

  http.post("/api/admin/agents/:id/restart", ({ params }) => {
    const agent = AGENTS.find((a) => a.id === params.id);
    if (!agent) return httpError(404, "Agent not found");
    if (
      !agent.provider ||
      (agent.lifecycle !== "terminated" && agent.lifecycle !== "failed")
    ) {
      return httpError(409, "Not restartable");
    }
    agent.lifecycle = "provisioning";
    agent.status = "unavailable";
    return HttpResponse.json(agent, { status: 202 });
  }),

  http.post("/api/admin/agents/:id/terminate", ({ params }) => {
    const agent = AGENTS.find((a) => a.id === params.id);
    if (!agent) return httpError(404, "Agent not found");
    if (
      !agent.provider ||
      (agent.lifecycle !== "running" && agent.lifecycle !== "provisioning")
    ) {
      return httpError(409, "Not terminable");
    }
    agent.lifecycle = "terminated";
    agent.status = "unavailable";
    return HttpResponse.json(agent, { status: 202 });
  }),

  http.delete("/api/admin/agents/:id", ({ params }) => {
    const idx = AGENTS.findIndex((a) => a.id === params.id);
    if (idx === -1) return httpError(404, "Agent not found");
    AGENTS.splice(idx, 1);
    return new HttpResponse(null, { status: 204 });
  }),

  http.delete("/api/admin/agents/:id/credential", ({ params }) => {
    const agent = AGENTS.find((a) => a.id === params.id);
    if (!agent) return httpError(404, "Agent not found");
    agent.status = "unavailable";
    return new HttpResponse(null, { status: 204 });
  }),
];
