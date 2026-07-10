import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { assistantApi } from "@/api/assistant";

export function useAssistantStatus(ws: string) {
  return useQuery({
    queryKey: ["workspace", ws, "assistant", "status"],
    queryFn: () => assistantApi.status(ws),
    enabled: !!ws,
  });
}

export function useConversations(ws: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ["workspace", ws, "assistant", "conversations"],
    queryFn: () => assistantApi.listConversations(ws),
    enabled: !!ws && options?.enabled !== false,
  });
}

export function useConversation(ws: string, id: string | null) {
  return useQuery({
    queryKey: ["workspace", ws, "assistant", "conversation", id],
    queryFn: () => assistantApi.getConversation(ws, id!),
    enabled: !!ws && !!id,
  });
}

export function useCreateConversation(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (title?: string) => assistantApi.createConversation(ws, title),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: ["workspace", ws, "assistant", "conversations"],
      });
    },
  });
}

export function useRenameConversation(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      assistantApi.renameConversation(ws, id, title),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({
        queryKey: ["workspace", ws, "assistant", "conversations"],
      });
      qc.invalidateQueries({
        queryKey: ["workspace", ws, "assistant", "conversation", id],
      });
    },
    onError: () => toast.error("Couldn't rename the conversation."),
  });
}

export function useDeleteConversation(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => assistantApi.deleteConversation(ws, id),
    onSuccess: () => {
      qc.invalidateQueries({
        queryKey: ["workspace", ws, "assistant", "conversations"],
      });
    },
    onError: () => toast.error("Couldn't delete the conversation."),
  });
}
