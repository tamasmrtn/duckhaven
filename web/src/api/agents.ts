import { get, post, del } from "./client";
import type { Agent } from "@/types/agent";

export const agentsApi = {
  list: () => get<Agent[]>("/agents"),

  adminList: () => get<Agent[]>("/admin/agents"),

  bootstrap: () =>
    post<{ token: string; expires_at: string }>("/admin/agents/bootstrap"),

  revoke: (id: string) => del(`/admin/agents/${id}/credential`),
};
