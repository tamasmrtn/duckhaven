import { useQuery } from "@tanstack/react-query";
import { usersApi } from "@/api/users";

export function useAdminUsers() {
  return useQuery({
    queryKey: ["admin", "users"],
    queryFn: usersApi.adminList,
  });
}
