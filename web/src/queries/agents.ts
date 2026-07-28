import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { agentsApi } from "@/api/agents";

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
