import { useEffect, useRef, useState } from "react";
import { useParams } from "@tanstack/react-router";
import { Bot, Plus, Send, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { EmptyState } from "@/components/app/EmptyState";
import {
  useConversation,
  useConversations,
  useCreateConversation,
  useDeleteConversation,
} from "@/queries/assistant";
import { cn } from "@/utils";
import { ToolCallCard } from "./ToolCallCard";
import { WriteApprovalDialog } from "./WriteApprovalDialog";
import { useAssistantChat } from "./useAssistantChat";

export function AssistantPage() {
  const { ws } = useParams({ from: "/$ws/assistant" });
  const { data: conversations = [], isLoading } = useConversations(ws);
  const createConversation = useCreateConversation(ws);
  const deleteConversation = useDeleteConversation(ws);
  const [picked, setPicked] = useState<string | null>(null);

  // Derive the active conversation: an explicit pick, else the most recent.
  const selectedId =
    picked && conversations.some((c) => c.id === picked)
      ? picked
      : (conversations[0]?.id ?? null);
  const setSelectedId = setPicked;

  const handleNew = async () => {
    const conv = await createConversation.mutateAsync(undefined);
    setSelectedId(conv.id);
  };

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-64 shrink-0 flex-col border-r border-[var(--border-subtle)]">
        <div className="flex items-center justify-between px-3 py-2">
          <span className="text-sm font-medium">Conversations</span>
          <Button
            size="sm"
            variant="ghost"
            onClick={handleNew}
            aria-label="New conversation"
          >
            <Plus className="size-4" />
          </Button>
        </div>
        <ScrollArea className="flex-1">
          {isLoading ? (
            <div className="space-y-2 p-3">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : (
            <ul className="px-2 pb-2">
              {conversations.map((conv) => (
                <li key={conv.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(conv.id)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-text-secondary hover:bg-accent",
                      selectedId === conv.id && "bg-accent text-text-primary",
                    )}
                  >
                    <span className="truncate">{conv.title}</span>
                    <Trash2
                      className="ml-auto size-3.5 shrink-0 opacity-0 hover:text-destructive group-hover:opacity-100"
                      role="button"
                      aria-label="Delete conversation"
                      onClick={(e) => {
                        e.stopPropagation();
                        void deleteConversation
                          .mutateAsync(conv.id)
                          .then(() => {
                            if (selectedId === conv.id) setSelectedId(null);
                          });
                      }}
                    />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </ScrollArea>
      </aside>

      {selectedId ? (
        <ConversationView ws={ws} conversationId={selectedId} />
      ) : (
        <div className="flex flex-1 items-center justify-center">
          <EmptyState
            icon={Bot}
            title="Ask the data assistant"
            description="Start a conversation to browse catalogs and run SQL in plain language."
            action={
              <Button onClick={handleNew}>
                <Plus className="mr-1 size-4" /> New conversation
              </Button>
            }
          />
        </div>
      )}
    </div>
  );
}

function ConversationView({
  ws,
  conversationId,
}: {
  ws: string;
  conversationId: string;
}) {
  const { data: detail } = useConversation(ws, conversationId);
  const chat = useAssistantChat(ws, conversationId);
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [detail?.transcript.length, chat.streamingText, chat.liveTools.length]);

  const submit = () => {
    const prompt = draft.trim();
    if (!prompt || chat.streaming) return;
    chat.send(prompt);
    setDraft("");
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ScrollArea className="flex-1">
        <div className="mx-auto flex max-w-2xl flex-col gap-3 p-4">
          {detail?.transcript.map((item, i) => (
            <Bubble key={i} role={item.role} text={item.text} />
          ))}

          {chat.streamingText && (
            <Bubble role="assistant" text={chat.streamingText} />
          )}

          {chat.streaming &&
            chat.liveTools.map((t, i) => (
              <p key={i} className="text-xs text-text-secondary" role="status">
                Running <span className="font-mono">{t.tool}</span>…
              </p>
            ))}

          {detail && detail.tool_calls.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium text-text-secondary">
                Activity
              </p>
              {detail.tool_calls.map((call) => (
                <ToolCallCard key={call.id} ws={ws} call={call} />
              ))}
            </div>
          )}

          {chat.error && (
            <p className="text-sm text-destructive" role="alert">
              {chat.error}
            </p>
          )}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>

      <div className="border-t border-[var(--border-subtle)] p-3">
        <div className="mx-auto flex max-w-2xl items-end gap-2">
          <textarea
            aria-label="Message"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="Ask about your data…"
            rows={1}
            className="max-h-40 flex-1 resize-none rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2 text-sm outline-none focus-visible:ring-1 focus-visible:ring-[var(--brand-slate-blue)]"
          />
          <Button
            onClick={submit}
            disabled={chat.streaming || !draft.trim()}
            aria-label="Send"
          >
            <Send className="size-4" />
          </Button>
        </div>
      </div>

      <WriteApprovalDialog
        pending={chat.pending}
        onResolve={chat.resolveApproval}
      />
    </div>
  );
}

function Bubble({ role, text }: { role: "user" | "assistant"; text: string }) {
  return (
    <div
      className={cn(
        "max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm",
        role === "user"
          ? "self-end bg-[var(--brand-slate-blue)] text-white"
          : "self-start bg-[var(--bg-surface)] border border-[var(--border-subtle)]",
      )}
    >
      {text}
    </div>
  );
}
