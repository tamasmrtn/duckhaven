import { useMe } from "@/queries/auth";
import { useWorkspaceMembers } from "@/queries/workspaces";

/**
 * Which semantic models the signed-in user may change. A writer edits a draft;
 * once a model is published or deprecated only a workspace owner can, and the
 * API refuses anyone else. Imported models are edited at their source.
 */
export function useCanEditModel(ws: string) {
  const { data: me } = useMe();
  // The member list is visible to owners only, so it is empty for anyone else.
  const { data: members = [] } = useWorkspaceMembers(ws);
  const isOwner = members.some(
    (m) => m.user_id === me?.id && m.role === "owner",
  );
  return (model: { provider: string; status: string }) =>
    model.provider === "native" && (model.status === "draft" || isOwner);
}
