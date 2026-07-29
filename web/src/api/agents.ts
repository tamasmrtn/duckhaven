import { get, post, put, patch, del } from "./client";
import type {
  Agent,
  AgentAccess,
  AgentAccessMode,
  AgentGrant,
  AgentGrantUpsert,
  AgentMonitoring,
  BootstrapToken,
  ComputeOptions,
  CreateElasticAgentBody,
  MonitoringWindow,
} from "@/types/agent";

export const agentsApi = {
  list: () => get<Agent[]>("/agents"),

  adminList: () => get<Agent[]>("/admin/agents"),

  adminGet: (id: string) => get<Agent>(`/admin/agents/${id}`),

  // One request per window change: the series share a bucket grid, so fetching
  // them separately would let a slow response leave two charts describing
  // different stretches of time.
  monitoring: (id: string, window: MonitoringWindow) =>
    get<AgentMonitoring>(`/admin/agents/${id}/monitoring?window=${window}`),

  bootstrap: () => post<BootstrapToken>("/admin/agents/bootstrap"),

  computeOptions: () => get<ComputeOptions>("/admin/agents/compute-options"),

  createElastic: (body: CreateElasticAgentBody) =>
    post<Agent>("/admin/agents/elastic", body),

  restart: (id: string) => post<Agent>(`/admin/agents/${id}/restart`),

  terminate: (id: string) => post<Agent>(`/admin/agents/${id}/terminate`),

  // Drops the agent's socket; it dials back in on its own. The one lifecycle
  // action available on a static agent (restart/terminate are elastic-only).
  disconnect: (id: string) => post<Agent>(`/admin/agents/${id}/disconnect`),

  remove: (id: string) => del(`/admin/agents/${id}`),

  revoke: (id: string) => del(`/admin/agents/${id}/credential`),

  // Mode, grants and the candidate principals in one response, so the grant
  // picker needs no second call.
  access: (id: string) => get<AgentAccess>(`/admin/agents/${id}/access`),

  setAccessMode: (id: string, mode: AgentAccessMode) =>
    patch<AgentAccess>(`/admin/agents/${id}/access-mode`, {
      access_mode: mode,
    }),

  upsertGrant: (id: string, body: AgentGrantUpsert) =>
    put<AgentGrant>(`/admin/agents/${id}/grants`, body),

  deleteGrant: (id: string, grantId: string) =>
    del(`/admin/agents/${id}/grants/${grantId}`),
};
