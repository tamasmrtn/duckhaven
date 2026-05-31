import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { workspacesApi } from "@/api/workspaces";

export function useWorkspaces() {
  return useQuery({
    queryKey: ["workspaces"],
    queryFn: workspacesApi.list,
  });
}

export function useWorkspace(ws: string) {
  return useQuery({
    queryKey: ["workspace", ws],
    queryFn: () => workspacesApi.get(ws),
    enabled: !!ws,
  });
}

export function useWorkspaceMembers(ws: string) {
  return useQuery({
    queryKey: ["workspace", ws, "members"],
    queryFn: () => workspacesApi.members(ws),
    enabled: !!ws,
  });
}

export function useCreateWorkspace() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      slug: string;
      name: string;
      storage_backend_id: string;
    }) => workspacesApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
    },
  });
}
