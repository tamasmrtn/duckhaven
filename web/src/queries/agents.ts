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

export function useRevokeAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => agentsApi.revoke(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["admin", "agents"] });
    },
  });
}
