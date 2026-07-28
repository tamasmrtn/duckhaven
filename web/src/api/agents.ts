import { get, post, del } from "./client";
import type {
  Agent,
  BootstrapToken,
  ComputeOptions,
  CreateElasticAgentBody,
} from "@/types/agent";

export const agentsApi = {
  list: () => get<Agent[]>("/agents"),

  adminList: () => get<Agent[]>("/admin/agents"),

  bootstrap: () => post<BootstrapToken>("/admin/agents/bootstrap"),

  computeOptions: () => get<ComputeOptions>("/admin/agents/compute-options"),

  createElastic: (body: CreateElasticAgentBody) =>
    post<Agent>("/admin/agents/elastic", body),

  restart: (id: string) => post<Agent>(`/admin/agents/${id}/restart`),

  terminate: (id: string) => post<Agent>(`/admin/agents/${id}/terminate`),

  remove: (id: string) => del(`/admin/agents/${id}`),

  revoke: (id: string) => del(`/admin/agents/${id}/credential`),
};
