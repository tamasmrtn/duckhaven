import { get } from "./client";
import type { AgentMetrics } from "@/types/agent";

export const metricsApi = {
  list: () => get<AgentMetrics[]>("/admin/agents/metrics"),
};
