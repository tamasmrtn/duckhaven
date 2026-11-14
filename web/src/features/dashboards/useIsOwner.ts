import { useMe } from "@/queries/auth";
import { useWorkspaceMembers } from "@/queries/workspaces";

/** Whether the signed-in person owns the workspace, and so may publish. */
export function useIsOwner(ws: string): boolean {
  const { data: me } = useMe();
  // The member list is visible to owners only, so it is empty for anyone else.
  const { data: members = [] } = useWorkspaceMembers(ws);
  return members.some((m) => m.user_id === me?.id && m.role === "owner");
}
