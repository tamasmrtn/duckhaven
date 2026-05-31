import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { setupApi, type FirstAdminInput } from "@/api/setup";

export function useSetupStatus() {
  return useQuery({
    queryKey: ["setup-status"],
    queryFn: setupApi.status,
    staleTime: 0,
    retry: false,
  });
}

export function useCreateFirstAdmin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ token, input }: { token: string; input: FirstAdminInput }) =>
      setupApi.createAdmin(token, input),
    onSuccess: (user) => {
      qc.setQueryData(["me"], user);
      qc.setQueryData(["setup-status"], { needs_admin: false });
    },
  });
}
