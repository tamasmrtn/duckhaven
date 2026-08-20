import {
  createContext,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";

/**
 * Bridge the worksheet editor exposes so the assistant can read its SQL and
 * propose edits to it. Registered by the worksheet; null when no editor is open.
 */
export interface EditorBridge {
  getSql: () => string;
  proposeEdit: (sql: string, explanation: string, scoped: boolean) => void;
  // The catalog currently USEd for unqualified names in the worksheet, so the
  // assistant resolves table names against what the user is looking at.
  getCatalog: () => string | null;
  // The current non-empty text selection in the worksheet, if any, so a proposed
  // edit can be scoped to just that fragment. Calling this also *captures* the
  // selection (arming the splice-back range), so it must be invoked at send-time,
  // not treated as a pure getter — see the implementation in WorksheetPage.
  captureSelection: () => { text: string; start: number; end: number } | null;
}

interface AssistantContextValue {
  open: boolean;
  // `seed`, when given, pre-fills the composer the next time AssistantPanel
  // mounts (e.g. from a "Fix with Assistant" entry point) — see seedPrompt.
  openPanel: (seed?: string) => void;
  closePanel: () => void;
  toggle: () => void;
  editorRef: RefObject<EditorBridge | null>;
  // AssistantPanel is unmounted while closed (see AppShell), so it reads this
  // once as its composer's initial state on mount rather than needing a
  // separate "consumed" flag. Cleared on every close (below) so reopening
  // plainly — no fresh seed — never re-applies stale error text.
  seedPrompt: string | null;
}

const AssistantContext = createContext<AssistantContextValue | null>(null);

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [seedPrompt, setSeedPrompt] = useState<string | null>(null);
  const editorRef = useRef<EditorBridge | null>(null);

  const value = useMemo<AssistantContextValue>(
    () => ({
      open,
      openPanel: (seed) => {
        setOpen(true);
        setSeedPrompt(seed ?? null);
      },
      closePanel: () => {
        setOpen(false);
        setSeedPrompt(null);
      },
      toggle: () => {
        if (open) {
          setOpen(false);
          setSeedPrompt(null);
        } else {
          setOpen(true);
        }
      },
      editorRef,
      seedPrompt,
    }),
    [open, seedPrompt],
  );

  return (
    <AssistantContext.Provider value={value}>
      {children}
    </AssistantContext.Provider>
  );
}

export function useAssistant(): AssistantContextValue {
  const ctx = useContext(AssistantContext);
  if (!ctx) {
    throw new Error("useAssistant must be used within an AssistantProvider");
  }
  return ctx;
}
