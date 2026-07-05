import { useCallback, useEffect, useRef, useState } from "react";
import { Sparkles, Plus, Send, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useConversation,
  useConversations,
  useCreateConversation,
} from "@/queries/assistant";
import { cn } from "@/utils";
import { useAssistant } from "./AssistantContext";
import { ToolCallCard } from "./ToolCallCard";
import { WriteApprovalDialog } from "./WriteApprovalDialog";
import { useAssistantChat } from "./useAssistantChat";

export function AssistantPanel({ ws }: { ws: string }) {
  const { closePanel, editorRef } = useAssistant();
  const { data: conversations = [] } = useConversations(ws);
  const createConversation = useCreateConversation(ws);
  const [picked, setPicked] = useState<string | null>(null);

  const effectiveId =
    picked && conversations.some((c) => c.id === picked)
      ? picked
      : (conversations[0]?.id ?? null);

  const { data: detail } = useConversation(ws, effectiveId);

  const getEditorSql = useCallback(
    () => editorRef.current?.getSql() ?? null,
    [editorRef],
  );
  const onProposeEdit = useCallback(
    (sql: string, explanation: string) =>
      editorRef.current?.proposeEdit(sql, explanation),
    [editorRef],
  );
  const chat = useAssistantChat(ws, effectiveId, {
    getEditorSql,
    onProposeEdit,
  });

  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [detail?.transcript.length, chat.streamingText, chat.liveTools.length]);

  const submit = async () => {
    const prompt = draft.trim();
    if (!prompt || chat.streaming) return;
    let id = effectiveId;
    if (!id) {
      const conv = await createConversation.mutateAsync(undefined);
      setPicked(conv.id);
      id = conv.id;
    }
    chat.send(prompt, id);
    setDraft("");
  };

  const startNew = async () => {
    const conv = await createConversation.mutateAsync(undefined);
    setPicked(conv.id);
  };

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] px-3 py-2">
        <Sparkles className="size-4 text-[var(--brand-yellow)]" />
        <span className="text-sm font-medium">Assistant</span>
        <div className="ml-auto flex items-center gap-1">
          {conversations.length > 0 && (
            <Select
              value={effectiveId ?? undefined}
              onValueChange={(v) => setPicked(v)}
            >
              <SelectTrigger
                className="h-7 w-36 text-xs"
                aria-label="Conversation"
              >
                <SelectValue placeholder="Conversation" />
              </SelectTrigger>
              <SelectContent>
                {conversations.map((c) => (
                  <SelectItem key={c.id} value={c.id} className="text-xs">
                    {c.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button
            size="icon"
            variant="ghost"
            className="size-7"
            onClick={startNew}
            aria-label="New conversation"
          >
            <Plus className="size-4" />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="size-7"
            onClick={closePanel}
            aria-label="Close assistant"
          >
            <X className="size-4" />
          </Button>
        </div>
      </div>

      {/* Thread */}
      <ScrollArea className="flex-1">
        <div className="flex flex-col gap-3 p-3">
          {!detail && (
            <p className="text-sm text-text-tertiary">
              Ask about your data, or ask me to write SQL in your worksheet.
            </p>
          )}
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

      {/* Composer */}
      <div className="border-t border-[var(--border-subtle)] p-2">
        <div className="flex items-end gap-2">
          <textarea
            aria-label="Message"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit();
              }
            }}
            placeholder="Ask about your data…"
            rows={1}
            className="max-h-40 flex-1 resize-none rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2 text-sm outline-none focus-visible:ring-1 focus-visible:ring-[var(--brand-slate-blue)]"
          />
          <Button
            size="icon"
            onClick={() => void submit()}
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
        "max-w-[90%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm",
        role === "user"
          ? "self-end bg-[var(--brand-slate-blue)] text-white"
          : "self-start border border-[var(--border-subtle)] bg-[var(--bg-surface)]",
      )}
    >
      {text}
    </div>
  );
}
