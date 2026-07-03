import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  serviceAccountsApi,
  type CreateServiceAccountInput,
  type IssuePatInput,
  type UpdateServiceAccountInput,
} from "@/api/service-accounts";

const KEY = ["admin", "service-accounts"];

export function useServiceAccounts() {
  return useQuery({
    queryKey: KEY,
    queryFn: serviceAccountsApi.adminList,
  });
}

export function useCreateServiceAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateServiceAccountInput) =>
      serviceAccountsApi.create(input),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useUpdateServiceAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      input,
    }: {
      id: string;
      input: UpdateServiceAccountInput;
    }) => serviceAccountsApi.update(id, input),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useDeleteServiceAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => serviceAccountsApi.remove(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useServiceAccountPats(id: string, enabled: boolean) {
  return useQuery({
    queryKey: [...KEY, id, "pats"],
    queryFn: () => serviceAccountsApi.listPats(id),
    enabled,
  });
}

// Issuing invalidates the PAT list + account list (pat_count) but, being a
// mutation, still hands the one-time secret back to the caller via onSuccess.
export function useIssuePat(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: IssuePatInput) =>
      serviceAccountsApi.issuePat(id, input),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...KEY, id, "pats"] });
      qc.invalidateQueries({ queryKey: KEY });
    },
  });
}

export function useRevokePat(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patId: string) => serviceAccountsApi.revokePat(id, patId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: [...KEY, id, "pats"] });
      qc.invalidateQueries({ queryKey: KEY });
    },
  });
}
