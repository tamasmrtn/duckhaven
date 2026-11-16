import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { authApi } from "@/api/auth";

/**
 * The account's navigation mode, kept in this browser only so the first paint
 * matches it before `/me` answers. The account stays the source of truth.
 */
export const NAV_MODE_CACHE_KEY = "dh-sidebar";

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: authApi.me,
    retry: false,
  });
}

export function useAuthMethods() {
  return useQuery({
    queryKey: ["auth", "methods"],
    queryFn: authApi.methods,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) =>
      authApi.login(email, password),
    onSuccess: (user) => {
      qc.setQueryData(["me"], user);
    },
  });
}

/**
 * Save the caller's own preferences. The caller updates the `["me"]` cache
 * itself before calling, so the change shows at once; a failure re-reads the
 * account so the screen shows what was really saved.
 */
export function useUpdateMe() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: authApi.updateMe,
    // One at a time, so quick changes reach the server in the order made.
    scope: { id: "me" },
    onError: () => {
      toast.error("Couldn't save your preference");
      void qc.invalidateQueries({ queryKey: ["me"] });
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: authApi.logout,
    onSuccess: () => {
      qc.clear();
      // The next person on this browser starts from the default until their
      // own account answers, not from this one's layout.
      try {
        localStorage.removeItem(NAV_MODE_CACHE_KEY);
      } catch {
        // Nothing was cached.
      }
    },
  });
}
