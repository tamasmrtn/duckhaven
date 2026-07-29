import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { agentsApi } from "@/api/agents";
import type {
  AgentAccessMode,
  AgentGrantUpsert,
  MonitoringWindow,
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

export function useAgentMonitoring(id: string, window: MonitoringWindow) {
  return useQuery({
    queryKey: ["admin", "agents", id, "monitoring", window],
    queryFn: () => agentsApi.monitoring(id, window),
    // Slower than the 2s live tiles on purpose: the coarsest bucket is a minute,
    // so a faster poll would redraw identical bars and fight the user's cursor
    // for the tooltip they are reading.
    refetchInterval: 30000,
    // Hold the previous window's data while the next one loads, so switching the
    // filter dims the charts instead of collapsing the page to skeletons.
    placeholderData: (prev) => prev,
  });
}

export function useBootstrapAgent() {
  return useMutation({
    mutationFn: agentsApi.bootstrap,
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
