import {
  useInfiniteQuery,
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { agentsApi } from "@/api/agents";
import type { AgentQueriesParams } from "@/api/agents";
import type {
  AgentAccessMode,
  AgentGrantUpsert,
  AgentMonitoring,
  MonitoringRange,
} from "@/types/agent";

export function useAgents() {
  return useQuery({
    queryKey: ["agents"],
    queryFn: agentsApi.list,
    refetchInterval: 5000,
  });
}

export function useAdminAgents() {
  return useQuery({
    queryKey: ["admin", "agents"],
    queryFn: agentsApi.adminList,
    refetchInterval: 5000,
  });
}

export function useAdminAgent(id: string) {
  return useQuery({
    queryKey: ["admin", "agents", id],
    queryFn: () => agentsApi.adminGet(id),
    refetchInterval: 5000,
  });
}

/**
 * How often a monitoring range is worth refetching.
 *
 * Its right-hand edge is live — the server fills the minute in progress from the
 * agent's own samples — so a range ending now polls at 15s, fast enough to follow
 * and slow enough not to fight the cursor for a tooltip. A multi-day range is
 * drawn in 30-minute or 2-hour buckets that barely move in a minute, and a zoomed
 * range that ends in the past never changes at all.
 */
export function monitoringRefetchInterval(
  range: MonitoringRange,
  now = Date.now(),
): number | false {
  if ("window" in range) {
    return range.window === "3d" || range.window === "7d" ? 60_000 : 15_000;
  }
  return now - Date.parse(range.end) > 2 * 60_000 ? false : 15_000;
}

export function useAgentMonitoring(id: string, range: MonitoringRange) {
  return useQuery<AgentMonitoring>({
    queryKey: ["admin", "agents", id, "monitoring", range],
    queryFn: () => agentsApi.monitoring(id, range),
    refetchInterval: () => monitoringRefetchInterval(range),
    // Hold the previous range's data while the next one loads, so switching the
    // range dims the charts instead of collapsing the page to skeletons.
    placeholderData: (prev) => prev,
  });
}

/** The agent's runs in a range or bucket, sorted by cost, a page at a time. */
export function useAgentQueries(
  id: string,
  params: Omit<AgentQueriesParams, "cursor">,
) {
  return useInfiniteQuery({
    queryKey: ["admin", "agents", id, "queries", params],
    queryFn: ({ pageParam }) =>
      agentsApi.queries(id, { ...params, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.cursor ?? undefined,
    placeholderData: (prev) => prev,
  });
}

export function useBootstrapAgent() {
  return useMutation({
    mutationFn: (runtimeId?: string) => agentsApi.bootstrap(runtimeId),
  });
}

export function useRuntimes() {
  return useQuery({
    queryKey: ["runtimes"],
    queryFn: agentsApi.runtimes,
    // The manifest only changes with a release.
    staleTime: Infinity,
  });
}

export function useComputeOptions() {
  return useQuery({
    queryKey: ["admin", "agents", "compute-options"],
    queryFn: agentsApi.computeOptions,
  });
}

export function useCreateElasticAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: agentsApi.createElastic,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "agents"] });
    },
  });
}

export function useRestartAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => agentsApi.restart(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "agents"] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export function useTerminateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => agentsApi.terminate(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "agents"] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export function useDeleteAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => agentsApi.remove(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "agents"] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export function useRevokeAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => agentsApi.revoke(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "agents"] });
    },
  });
}

export function useDisconnectAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => agentsApi.disconnect(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "agents"] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

// --- per-agent access control ------------------------------------------------

export function useAgentAccess(id: string, enabled = true) {
  return useQuery({
    queryKey: ["admin", "agents", id, "access"],
    queryFn: () => agentsApi.access(id),
    enabled,
  });
}

/**
 * Every access mutation invalidates the agent lists too: changing the mode or a
 * grant changes who sees the agent and what `access_tier` each of them gets.
 */
function useAccessMutation<TArgs>(
  id: string,
  mutationFn: (args: TArgs) => Promise<unknown>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "agents", id, "access"] });
      qc.invalidateQueries({ queryKey: ["admin", "agents"] });
      qc.invalidateQueries({ queryKey: ["agents"] });
    },
  });
}

export function useSetAgentAccessMode(id: string) {
  return useAccessMutation<AgentAccessMode>(id, (mode) =>
    agentsApi.setAccessMode(id, mode),
  );
}

export function useUpsertAgentGrant(id: string) {
  return useAccessMutation<AgentGrantUpsert>(id, (body) =>
    agentsApi.upsertGrant(id, body),
  );
}

export function useDeleteAgentGrant(id: string) {
  return useAccessMutation<string>(id, (grantId) =>
    agentsApi.deleteGrant(id, grantId),
  );
}
