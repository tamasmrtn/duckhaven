import { useMutation, useQueryClient } from "@tanstack/react-query";
import { workspacesApi } from "@/api/workspaces";

export function useUpdateWorkspace(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { name?: string; description?: string }) =>
      workspacesApi.update(ws, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      qc.invalidateQueries({ queryKey: ["workspace", ws] });
    },
  });
}

export function useDeleteWorkspace(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => workspacesApi.remove(ws),
    onSuccess: () => {
      // The deleted workspace must vanish from WorkspaceSwitcher immediately,
      // not linger until its own stale cache entry happens to expire.
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      qc.removeQueries({ queryKey: ["workspace", ws] });
    },
  });
}
