import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  usersApi,
  type CreateUserInput,
  type UpdateUserInput,
} from "@/api/users";

export function useAdminUsers() {
  return useQuery({
    queryKey: ["admin", "users"],
    queryFn: usersApi.adminList,
  });
}

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateUserInput) => usersApi.create(input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
  });
}

export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateUserInput }) =>
      usersApi.update(id, input),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
  });
}

export function useRevokeSessions() {
  return useMutation({
    mutationFn: (id: string) => usersApi.revokeSessions(id),
  });
}

export function useUserWorkspaces(userId: string, enabled = true) {
  return useQuery({
    queryKey: ["admin", "users", userId, "workspaces"],
    queryFn: () => usersApi.workspaces(userId),
    enabled,
  });
}

export function useSetUserWorkspaceRole(userId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ ws, role }: { ws: string; role: string }) =>
      usersApi.setWorkspaceRole(userId, ws, role),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["admin", "users", userId, "workspaces"],
      });
    },
  });
}

export function useRemoveUserWorkspace(userId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ws: string) => usersApi.removeWorkspace(userId, ws),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["admin", "users", userId, "workspaces"],
      });
    },
  });
}
