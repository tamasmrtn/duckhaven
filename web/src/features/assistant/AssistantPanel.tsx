import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  Sparkles,
  Plus,
  Send,
  CircleStop,
  X,
  ChevronRight,
  ChevronDown,
  Loader2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  useAssistantStatus,
  useConversation,
  useConversations,
  useCreateConversation,
} from "@/queries/assistant";
import { useCatalogs } from "@/queries/catalogs";
import { EmptyState } from "@/components/ui/empty-state";
import { cn } from "@/utils";
import { useAssistant } from "./AssistantContext";
import { ConversationList } from "./ConversationList";
import { Markdown } from "./Markdown";
import { ThinkingStatus } from "./ThinkingStatus";
import { ToolCallCard } from "./ToolCallCard";
import { WriteApprovalDialog } from "./WriteApprovalDialog";
import { useAssistantChat } from "./useAssistantChat";

// Shared pill/chip styling for the starter prompts, "View full result" link, and
// "Ask a follow-up" button, so a tweak stays a single edit.
const chipClass =
  "rounded-full border border-[var(--border-subtle)] px-3 py-1 text-xs text-text-secondary hover:border-[var(--brand-slate-blue)] hover:text-text-primary";

// Throttles a fast-changing string to at most one update per `delayMs`, so an
// aria-live region can announce streamed text without firing per token.
function useThrottledText(value: string, delayMs: number): string {
  const [throttled, setThrottled] = useState(value);
  const lastFiredAtRef = useRef(0);
  useEffect(() => {
    const elapsed = Date.now() - lastFiredAtRef.current;
    if (elapsed >= delayMs) {
      lastFiredAtRef.current = Date.now();
      setThrottled(value);
      return;
    }
    const timeout = setTimeout(() => {
      lastFiredAtRef.current = Date.now();
      setThrottled(value);
    }, delayMs - elapsed);
    return () => clearTimeout(timeout);
  }, [value, delayMs]);
  return throttled;
}

const GENERIC_STARTER_PROMPTS = [
  "What data do I have access to?",
  "Help me write a SQL query.",
];

/** 2-3 example prompts scoped to the workspace's actual catalogs, so the empty
 * state suggests something concrete instead of a blank composer. */
function starterPrompts(catalogSlugs: string[]): string[] {
  if (catalogSlugs.length === 0) return GENERIC_STARTER_PROMPTS;
  const [first, second] = catalogSlugs;
  const prompts = [`What tables are in ${first}?`];
  if (second) {
    prompts.push(`Compare the schemas in ${first} and ${second}.`);
  } else {
    prompts.push(`Describe a table in ${first}.`);
  }
  prompts.push(`Show me a sample of data from ${first}.`);
  return prompts;
}

export function AssistantPanel({ ws }: { ws: string }) {
  const { closePanel, editorRef, seedPrompt } = useAssistant();
  const { data: status } = useAssistantStatus(ws);
  const enabled = status?.enabled === true;
  const disabled = status?.enabled === false;
  const {
    data: conversations = [],
    isLoading: conversationsLoading,
    isError: conversationsError,
  } = useConversations(ws, { enabled });
  const { data: catalogs = [] } = useCatalogs(ws);
  const createConversation = useCreateConversation(ws);
  const [picked, setPicked] = useState<string | null>(null);

  const effectiveId =
    picked && conversations.some((c) => c.id === picked)
      ? picked
      : (conversations[0]?.id ?? null);

  const {
    data: detail,
    isLoading: detailLoading,
    isError: detailError,
  } = useConversation(ws, effectiveId);
  // A conversation is only fetched when one is selected, so its loading flag only
  // counts when there's an id to load.
  const loadingThread =
    conversationsLoading || (!!effectiveId && detailLoading);
  const threadLoadError = conversationsError || detailError;

  const getEditorSql = useCallback(
    () => editorRef.current?.getSql() ?? null,
    [editorRef],
  );
  const getCatalog = useCallback(
    () => editorRef.current?.getCatalog() ?? null,
    [editorRef],
  );
  const captureSelection = useCallback(
    () => editorRef.current?.captureSelection() ?? null,
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
    captureSelection,
    onProposeEdit,
  });
  const throttledStreamingText = useThrottledText(chat.streamingText, 750);

  // The panel unmounts while closed (see AppShell), so a seed from
  // openPanel(seed) — e.g. "Fix with Assistant" on a failed query — is only
  // ever this component's *initial* draft, never re-applied over an
  // in-progress edit.
  const [draft, setDraft] = useState(() => seedPrompt ?? "");
  // Activity (tool-call trace) is collapsed by default — it can get long.
  const [activityOpen, setActivityOpen] = useState(false);
  // Keyed by conversation id so switching to another long conversation
  // re-shows the notice instead of staying dismissed forever.
  const [dismissedTruncationFor, setDismissedTruncationFor] = useState<
    string | null
  >(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [detail?.transcript.length, chat.streamingText, chat.liveTools.length]);
  useEffect(() => {
    // Focus (not send) so the user can review or redact before it goes to
    // the model — the seed text is a raw engine error, which can echo data.
    if (seedPrompt) composerRef.current?.focus();
    // Mount-only: this mirrors the draft initializer above, which only ever
    // looks at seedPrompt on this same initial render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const submit = async (overrideText?: string) => {
    const prompt = (overrideText ?? draft).trim();
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
            <ConversationList
              ws={ws}
              conversations={conversations}
              activeId={effectiveId}
              onSelect={(id) => setPicked(id)}
            />
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

      {!disabled &&
        detail?.history_truncated &&
        dismissedTruncationFor !== effectiveId && (
          <div className="flex items-center justify-between gap-2 border-b border-[var(--border-subtle)] px-3 py-1.5">
            <p className="text-sm text-text-tertiary" role="status">
              This conversation is long — earlier messages are no longer part of
              its context.
            </p>
            <Button
              size="icon"
              variant="ghost"
              className="size-6 shrink-0"
              onClick={() => setDismissedTruncationFor(effectiveId)}
              aria-label="Dismiss notice"
            >
              <X className="size-3.5" />
            </Button>
          </div>
        )}

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
            {threadLoadError ? (
              <p className="text-sm text-destructive" role="alert">
                Couldn't load your conversations. Reopen the panel to try again.
              </p>
            ) : (
              loadingThread && (
                <div className="flex justify-center p-6 text-text-tertiary">
                  <Loader2
                    className="size-4 animate-spin"
                    aria-label="Loading"
                  />
                </div>
              )
            )}
            {!threadLoadError &&
              !loadingThread &&
              (!detail || detail.transcript.length === 0) &&
              !chat.pendingUserMessage &&
              !chat.streaming &&
              !chat.error &&
              !chat.stopped && (
                <EmptyState
                  icon={Sparkles}
                  title="Ask about your data"
                  description="Or ask me to write SQL in your worksheet."
                  action={
                    <div className="flex flex-col items-center gap-1.5">
                      {starterPrompts(catalogs.map((c) => c.slug)).map(
                        (prompt) => (
                          <button
                            key={prompt}
                            type="button"
                            onClick={() => void submit(prompt)}
                            className={chipClass}
                          >
                            {prompt}
                          </button>
                        ),
                      )}
                    </div>
                  }
                />
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
            {chat.error && (
              <div className="max-w-[90%] self-start space-y-1.5">
                <p className="text-sm text-destructive" role="alert">
                  {chat.error}
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  onClick={chat.regenerate}
                >
                  Retry
                </Button>
              </div>
            )}
            {chat.stopped && (
              <div className="max-w-[90%] self-start space-y-1.5">
                <p className="text-sm text-text-tertiary" role="status">
                  Stopped.
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 text-xs"
                  onClick={chat.regenerate}
                >
                  Retry
                </Button>
              </div>
            )}
            {chat.streamingText && (
              <>
                <Bubble role="assistant" text={chat.streamingText} />
                {/* Throttled so screen readers get periodic updates, not one per token. */}
                <div aria-live="polite" className="sr-only">
                  {throttledStreamingText}
                </div>
              </>
            )}
            {chat.streaming && (
              <ThinkingStatus
                currentTool={
                  chat.liveTools[chat.liveTools.length - 1]?.tool ?? null
                }
              />
            )}
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
            {!chat.streaming &&
              !chat.pending &&
              !chat.error &&
              chat.canRegenerate &&
              detail &&
              detail.transcript.length > 0 &&
              detail.transcript[detail.transcript.length - 1].role ===
                "assistant" && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-6 self-start text-xs text-text-tertiary"
                  onClick={chat.regenerate}
                >
                  Regenerate
                </Button>
              )}
            {!chat.streaming &&
              !chat.pending &&
              detail &&
              detail.transcript.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {(() => {
                    const lastQuery = [...detail.tool_calls]
                      .reverse()
                      .find((c) => c.tool === "run_sql" && c.query_id);
                    return (
                      lastQuery && (
                        <Link
                          to="/$ws/queries/$queryId"
                          params={{ ws, queryId: lastQuery.query_id! }}
                          className={chipClass}
                        >
                          View full result
                        </Link>
                      )
                    );
                  })()}
                  {(() => {
                    const lastWithTables = [...detail.tool_calls]
                      .reverse()
                      .find((c) => c.tool === "run_sql" && c.tables?.length);
                    return lastWithTables?.tables?.map((t) => (
                      <Link
                        key={`${t.catalog}.${t.schema_name}.${t.table}`}
                        to="/$ws/catalog/$catalog/$schema/$table"
                        params={{
                          ws,
                          catalog: t.catalog,
                          schema: t.schema_name,
                          table: t.table,
                        }}
                        className={chipClass}
                      >
                        Open {t.table} in Catalog
                      </Link>
                    ));
                  })()}
                  <button
                    type="button"
                    onClick={() => composerRef.current?.focus()}
                    className={chipClass}
                  >
                    Ask a follow-up
                  </button>
                </div>
              )}
            <div ref={bottomRef} />
          </div>
        </ScrollArea>
      )}

      {/* Composer */}
      <div className="border-t border-[var(--border-subtle)] p-2">
        <div className="flex items-end gap-2">
          <textarea
            ref={composerRef}
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
        <pre className="mt-2 overflow-x-auto rounded bg-[var(--bg-code)] p-2 font-mono text-xs text-[var(--text-code)]">
          {sql}
        </pre>
      )}
    </div>
  );
}
