import { get, post, del } from "./client";
import type {
  Agent,
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

  remove: (id: string) => del(`/admin/agents/${id}`),

  revoke: (id: string) => del(`/admin/agents/${id}/credential`),
};
