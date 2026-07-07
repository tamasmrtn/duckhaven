import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { streamApproval, streamMessage } from "@/api/assistant";
import type { AssistantFrame, PendingApproval } from "@/types/assistant";

interface LiveTool {
  tool: string;
}

interface ChatOptions {
  // Current worksheet SQL to send as context (so the assistant can edit it).
  getEditorSql?: () => string | null;
  // Apply an assistant-proposed edit to the worksheet editor.
  onProposeEdit?: (sql: string, explanation: string) => void;
}

/**
 * Drives one conversation's streaming turns: consumes SSE frames into live
 * state (streamed text, tool activity, a pending write approval, proposed editor
 * edits), and reloads the persisted transcript when a turn settles.
 */
export function useAssistantChat(
  ws: string,
  conversationId: string | null,
  options: ChatOptions = {},
) {
  const { getEditorSql, onProposeEdit } = options;
  const qc = useQueryClient();
  const [streaming, setStreaming] = useState(false);
  const [streamingText, setStreamingText] = useState("");
  const [liveTools, setLiveTools] = useState<LiveTool[]>([]);
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The just-sent user message, echoed immediately so it appears above the
  // streaming reply instead of only after the turn's transcript refetch.
  const [pendingUserMessage, setPendingUserMessage] = useState<string | null>(
    null,
  );
  // Aborts the in-flight turn's stream (the Stop button).
  const abortRef = useRef<AbortController | null>(null);

  const consume = useCallback(
    async (
      id: string,
      frames: AsyncGenerator<AssistantFrame, void, unknown>,
    ) => {
      setStreaming(true);
      setError(null);
      setStreamingText("");
      setLiveTools([]);
      try {
        for await (const frame of frames) {
          if (frame.type === "token") {
            setStreamingText((t) => t + frame.text);
          } else if (frame.type === "tool_call") {
            setLiveTools((ts) => [...ts, { tool: frame.tool }]);
          } else if (frame.type === "approval_required") {
            setPending({
              tool_call_id: frame.tool_call_id,
              tool: frame.tool,
              sql: frame.sql,
            });
          } else if (frame.type === "propose_edit") {
            onProposeEdit?.(frame.sql, frame.explanation);
          } else if (frame.type === "error") {
            setError(frame.message);
          }
        }
      } catch (err) {
        if (!(abortRef.current?.signal.aborted || isAbort(err))) {
          setError(
            err instanceof Error ? err.message : "The assistant failed.",
          );
        }
      } finally {
        // Cancelling the reader on Stop ends the loop without throwing, so the
        // aborted state is read from the signal here rather than a caught error.
        const aborted = abortRef.current?.signal.aborted === true;
        setStreaming(false);
        abortRef.current = null;
        if (aborted) {
          // Stopped by the user: keep the partial reply and the user's message on
          // screen. The server finishes and persists the turn, so it reconciles
          // on the next refetch (e.g. the next send).
          setLiveTools([]);
        } else {
          await qc.invalidateQueries({
            queryKey: ["workspace", ws, "assistant", "conversation", id],
          });
          await qc.invalidateQueries({
            queryKey: ["workspace", ws, "assistant", "conversations"],
          });
          setStreamingText("");
          setLiveTools([]);
          // Cleared after the refetch settles, so the persisted transcript (which
          // now includes this message) replaces the optimistic echo seamlessly.
          setPendingUserMessage(null);
        }
      }
    },
    [ws, qc, onProposeEdit],
  );

  const send = useCallback(
    (prompt: string, id: string) => {
      if (streaming) return;
      setPendingUserMessage(prompt);
      const controller = new AbortController();
      abortRef.current = controller;
      void consume(
        id,
        streamMessage(ws, id, prompt, {
          editorSql: getEditorSql?.() ?? null,
          signal: controller.signal,
        }),
      );
    },
    [ws, streaming, consume, getEditorSql],
  );

  const resolveApproval = useCallback(
    (approved: boolean) => {
      if (!conversationId || !pending) return;
      const request = pending;
      setPending(null);
      const controller = new AbortController();
      abortRef.current = controller;
      void consume(
        conversationId,
        streamApproval(ws, conversationId, request.tool_call_id, approved, {
          signal: controller.signal,
        }),
      );
    },
    [ws, conversationId, pending, consume],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return {
    streaming,
    streamingText,
    liveTools,
    pending,
    error,
    pendingUserMessage,
    send,
    resolveApproval,
    stop,
  };
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}
