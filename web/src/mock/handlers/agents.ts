import { http, HttpResponse } from "msw";
import {
  AGENTS,
  AGENT_GRANTS,
  AGENT_GRANT_PRINCIPALS,
} from "../fixtures/agents";
import { makeEmptyMonitoring, makeMonitoring } from "../fixtures/monitoring";
import { nextBootstrapToken } from "../lib/seed";
import { httpError } from "../lib/errors";
import type {
  Agent,
  AgentAccess,
  AgentAccessMode,
  AgentGrant,
  AgentGrantUpsert,
  MonitoringWindow,
} from "@/types/agent";
import { MONITORING_WINDOWS } from "@/types/agent";

// Mirrors the backend's ACI ranges + rate defaults.
const PRICE_VCPU = 0.0486;
const PRICE_MEM = 0.0054;

let elasticSeq = 0;
let grantSeq = 0;

function accessPayload(agent: Agent): AgentAccess {
  return {
    agent_id: agent.id,
    access_mode: agent.access_mode ?? "open",
    grants: AGENT_GRANTS[agent.id] ?? [],
    principals: AGENT_GRANT_PRINCIPALS,
  };
}

// Sibling routes under /api/admin/agents/ that are literal path segments, not
// agent ids. Returning undefined for these lets MSW fall through to their own
// handlers.
const LITERAL_AGENT_PATHS = [
  "metrics",
  "compute-options",
  "bootstrap",
  "elastic",
];

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

  http.get("/api/admin/agents/:id", ({ params }) => {
    // `:id` would otherwise swallow the sibling literal routes. Declaration
    // order is not enough here: /api/admin/agents/metrics is served from
    // handlers/metrics.ts, so the guard has to live in the pattern that would
    // shadow it rather than in the order the two files happen to be composed in.
    if (LITERAL_AGENT_PATHS.includes(String(params.id))) return;
    const agent = AGENTS.find((a) => a.id === params.id);
    if (!agent) return httpError(404, "Agent not found");
    return HttpResponse.json(agent);
  }),

  http.get("/api/admin/agents/:id/monitoring", ({ params, request }) => {
    const agent = AGENTS.find((a) => a.id === params.id);
    if (!agent) return httpError(404, "Agent not found");
    const raw = new URL(request.url).searchParams.get("window") ?? "8h";
    if (!MONITORING_WINDOWS.includes(raw as MonitoringWindow)) {
      return httpError(422, `Unknown window '${raw}'`);
    }
    const window = raw as MonitoringWindow;
    // An agent that never connected has nothing to show — the case the empty
    // state and the null-vs-zero rendering exist for.
    return HttpResponse.json(
      agent.capabilities ? makeMonitoring(window) : makeEmptyMonitoring(window),
    );
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

  http.post("/api/admin/agents/:id/disconnect", ({ params }) => {
    const agent = AGENTS.find((a) => a.id === params.id);
    if (!agent) return httpError(404, "Agent not found");
    agent.status = "unavailable";
    return HttpResponse.json(agent, { status: 202 });
  }),

  // --- per-agent access control ---------------------------------------------

  http.get("/api/admin/agents/:id/access", ({ params }) => {
    const agent = AGENTS.find((a) => a.id === params.id);
    if (!agent) return httpError(404, "Agent not found");
    return HttpResponse.json(accessPayload(agent));
  }),

  http.patch(
    "/api/admin/agents/:id/access-mode",
    async ({ params, request }) => {
      const agent = AGENTS.find((a) => a.id === params.id);
      if (!agent) return httpError(404, "Agent not found");
      const body = (await request.json()) as { access_mode: AgentAccessMode };
      agent.access_mode = body.access_mode;
      return HttpResponse.json(accessPayload(agent));
    },
  ),

  http.put("/api/admin/agents/:id/grants", async ({ params, request }) => {
    const agent = AGENTS.find((a) => a.id === params.id);
    if (!agent) return httpError(404, "Agent not found");
    const body = (await request.json()) as AgentGrantUpsert;
    if ((body.user_id == null) === (body.workspace_id == null)) {
      return httpError(
        422,
        "exactly one of user_id or workspace_id is required",
      );
    }
    if (body.workspace_id != null && body.tier === "admin") {
      return httpError(
        422,
        "a workspace grant cannot exceed the 'operate' tier",
      );
    }
    const grants = (AGENT_GRANTS[agent.id] ??= []);
    const existing = grants.find((g) =>
      body.user_id != null
        ? g.user_id === body.user_id
        : g.workspace_id === body.workspace_id,
    );
    if (existing) {
      existing.tier = body.tier;
      return HttpResponse.json(existing);
    }
    const principal = AGENT_GRANT_PRINCIPALS.find(
      (p) => p.id === (body.user_id ?? body.workspace_id),
    );
    grantSeq += 1;
    const grant: AgentGrant = {
      id: `ag-grant-${grantSeq}`,
      user_id: body.user_id ?? null,
      user_name: body.user_id ? (principal?.name ?? null) : null,
      workspace_id: body.workspace_id ?? null,
      workspace_name: body.workspace_id ? (principal?.name ?? null) : null,
      tier: body.tier,
      created_at: new Date().toISOString(),
    };
    grants.push(grant);
    return HttpResponse.json(grant, { status: 201 });
  }),

  http.delete("/api/admin/agents/:id/grants/:grantId", ({ params }) => {
    const grants = AGENT_GRANTS[String(params.id)] ?? [];
    const idx = grants.findIndex((g) => g.id === params.grantId);
    if (idx === -1) return httpError(404, "Grant not found");
    grants.splice(idx, 1);
    return new HttpResponse(null, { status: 204 });
  }),
];
