import { useQuery } from "@tanstack/react-query";
import { metricsApi } from "@/api/metrics";

export function useAgentMetrics() {
  return useQuery({
    queryKey: ["admin", "metrics"],
    queryFn: metricsApi.list,
    // 2s cadence matches the agent's sampler; each response carries the full
    // recent window so the chart never gaps on a missed poll.
    refetchInterval: 2000,
  });
}
