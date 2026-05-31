import { get, post, del } from "./client";
import type { Agent, BootstrapToken } from "@/types/agent";

export const agentsApi = {
  list: () => get<Agent[]>("/agents"),

  adminList: () => get<Agent[]>("/admin/agents"),

  bootstrap: () => post<BootstrapToken>("/admin/agents/bootstrap"),

  revoke: (id: string) => del(`/admin/agents/${id}/credential`),
};
