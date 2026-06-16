import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { maintenanceApi } from "@/api/maintenance";
import type { PolicyUpdate } from "@/types/maintenance";

export function useDeploymentHealth() {
  return useQuery({
    queryKey: ["maintenance", "health"],
    queryFn: maintenanceApi.deploymentHealth,
  });
}

export function useWorkspaceHealth(ws: string) {
  return useQuery({
    queryKey: ["maintenance", "health", ws],
    queryFn: () => maintenanceApi.workspaceHealth(ws),
  });
}

export function useTableHealth(ws: string, schema: string, table: string) {
  return useQuery({
    queryKey: ["maintenance", "health", ws, schema, table],
    queryFn: () => maintenanceApi.tableHealth(ws, schema, table),
    // A table with no scan yet 404s; don't hammer it.
    retry: false,
  });
}

export function useRecommendations(status = "open") {
  return useQuery({
    queryKey: ["maintenance", "recommendations", status],
    queryFn: () => maintenanceApi.recommendations(status),
  });
}

export function useDismissRecommendation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => maintenanceApi.dismiss(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["maintenance"] });
    },
  });
}

export function useMaintenancePolicy() {
  return useQuery({
    queryKey: ["maintenance", "policy"],
    queryFn: maintenanceApi.policy,
  });
}

export function useUpdatePolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: PolicyUpdate) => maintenanceApi.updatePolicy(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["maintenance", "policy"] });
    },
  });
}

export function useTriggerScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => maintenanceApi.scan(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["maintenance"] });
    },
  });
}
