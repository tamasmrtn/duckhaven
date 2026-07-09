import { useCallback, useEffect, useRef, useState } from "react";
import {
  Sparkles,
  Plus,
  Send,
  CircleStop,
  X,
  ChevronRight,
  ChevronDown,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
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
  useAssistantStatus,
  useConversation,
  useConversations,
  useCreateConversation,
} from "@/queries/assistant";
import { EmptyState } from "@/components/app/EmptyState";
import { cn } from "@/utils";
import { useAssistant } from "./AssistantContext";
import { Markdown } from "./Markdown";
import { ToolCallCard } from "./ToolCallCard";
import { WriteApprovalDialog } from "./WriteApprovalDialog";
import { useAssistantChat } from "./useAssistantChat";

export function AssistantPanel({ ws }: { ws: string }) {
  const { closePanel, editorRef } = useAssistant();
  const { data: status } = useAssistantStatus(ws);
  const enabled = status?.enabled === true;
  const disabled = status?.enabled === false;
  const { data: conversations = [] } = useConversations(ws, { enabled });
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
  const getCatalog = useCallback(
    () => editorRef.current?.getCatalog() ?? null,
    [editorRef],
  );
  const getSelection = useCallback(
    () => editorRef.current?.getSelection() ?? null,
    [editorRef],
  );
  const onProposeEdit = useCallback(
    (sql: string, explanation: string, scoped: boolean) =>
      editorRef.current?.proposeEdit(sql, explanation, scoped),
    [editorRef],
  );
  const chat = useAssistantChat(ws, effectiveId, {
    getEditorSql,
    getCatalog,
    getSelection,
    onProposeEdit,
  });

  const [draft, setDraft] = useState("");
  // Activity (tool-call trace) is collapsed by default — it can get long.
  const [activityOpen, setActivityOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [detail?.transcript.length, chat.streamingText, chat.liveTools.length]);

  const submit = async () => {
    const prompt = draft.trim();
    if (!prompt || chat.streaming || disabled) return;
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
        {detail && (
          <Badge
            variant="outline"
            className="font-normal text-text-tertiary"
            title="Tokens used in this conversation (input / output)"
          >
            {detail.total_input_tokens.toLocaleString()} in ·{" "}
            {detail.total_output_tokens.toLocaleString()} out
          </Badge>
        )}
        <div className="ml-auto flex items-center gap-1">
          {!disabled && conversations.length > 0 && (
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
          {!disabled && (
            <Button
              size="icon"
              variant="ghost"
              className="size-7"
              onClick={startNew}
              aria-label="New conversation"
            >
              <Plus className="size-4" />
            </Button>
          )}
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

      {/* Thread (or the disabled notice) */}
      {disabled ? (
        <div className="flex flex-1 items-center justify-center p-6">
          <EmptyState
            icon={Sparkles}
            title="Assistant is turned off"
            description="An administrator hasn't enabled the AI assistant for this deployment. Enable it (set ASSISTANT_ENABLED), or ask a DuckHaven admin to turn it on."
          />
        </div>
      ) : (
        <ScrollArea className="flex-1">
          <div className="flex flex-col gap-3 p-3">
            {!detail && !chat.pendingUserMessage && !chat.streaming && (
              <p className="text-sm text-text-tertiary">
                Ask about your data, or ask me to write SQL in your worksheet.
              </p>
            )}
            {detail?.transcript.map((item, i) => (
              <Bubble
                key={i}
                role={item.role}
                text={item.text}
                sql={item.sql}
              />
            ))}
            {chat.pendingUserMessage && (
              <Bubble role="user" text={chat.pendingUserMessage} />
            )}
            {chat.streamingText && (
              <Bubble role="assistant" text={chat.streamingText} />
            )}
            {chat.streaming &&
              chat.liveTools.map((t, i) => (
                <p
                  key={i}
                  className="text-xs text-text-secondary"
                  role="status"
                >
                  Running <span className="font-mono">{t.tool}</span>…
                </p>
              ))}
            {detail && detail.tool_calls.length > 0 && (
              <div className="space-y-1">
                <button
                  type="button"
                  onClick={() => setActivityOpen((o) => !o)}
                  aria-expanded={activityOpen}
                  className="flex items-center gap-1 text-xs font-medium text-text-secondary hover:text-text-primary"
                >
                  {activityOpen ? (
                    <ChevronDown className="size-3.5" />
                  ) : (
                    <ChevronRight className="size-3.5" />
                  )}
                  Activity ({detail.tool_calls.length})
                </button>
                {activityOpen &&
                  detail.tool_calls.map((call) => (
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
      )}

      {/* Composer */}
      <div className="border-t border-[var(--border-subtle)] p-2">
        <div className="flex items-end gap-2">
          <textarea
            aria-label="Message"
            value={draft}
            disabled={disabled}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void submit();
              }
            }}
            placeholder={
              disabled ? "Assistant is turned off" : "Ask about your data…"
            }
            rows={1}
            className="max-h-40 flex-1 resize-none rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2 text-sm outline-none focus-visible:ring-1 focus-visible:ring-[var(--brand-slate-blue)] disabled:opacity-50"
          />
          {chat.streaming ? (
            <Button
              size="icon"
              variant="outline"
              onClick={chat.stop}
              aria-label="Stop"
            >
              <CircleStop className="size-4" />
            </Button>
          ) : (
            <Button
              size="icon"
              onClick={() => void submit()}
              disabled={disabled || !draft.trim()}
              aria-label="Send"
            >
              <Send className="size-4" />
            </Button>
          )}
        </div>
      </div>

      <WriteApprovalDialog
        pending={chat.pending}
        onResolve={chat.resolveApproval}
      />
    </div>
  );
}

function Bubble({
  role,
  text,
  sql,
}: {
  role: "user" | "assistant";
  text: string;
  sql?: string | null;
}) {
  return (
    <div
      className={cn(
        "max-w-[90%] rounded-lg px-3 py-2 text-sm",
        role === "user"
          ? "self-end whitespace-pre-wrap bg-[var(--brand-slate-blue)] text-white"
          : "min-w-0 self-start border border-[var(--border-subtle)] bg-[var(--bg-surface)]",
      )}
    >
      {role === "assistant" ? <Markdown>{text}</Markdown> : text}
      {sql && (
        <pre className="mt-2 overflow-x-auto rounded bg-[var(--bg-code)] p-2 font-mono text-xs text-white">
          {sql}
        </pre>
      )}
    </div>
  );
}
