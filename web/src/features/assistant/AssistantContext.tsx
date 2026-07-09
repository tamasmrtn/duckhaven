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
  proposeEdit: (sql: string, explanation: string) => void;
  // The catalog currently USEd for unqualified names in the worksheet, so the
  // assistant resolves table names against what the user is looking at.
  getCatalog: () => string | null;
}

interface AssistantContextValue {
  open: boolean;
  openPanel: () => void;
  closePanel: () => void;
  toggle: () => void;
  editorRef: RefObject<EditorBridge | null>;
}

const AssistantContext = createContext<AssistantContextValue | null>(null);

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const editorRef = useRef<EditorBridge | null>(null);

  const value = useMemo<AssistantContextValue>(
    () => ({
      open,
      openPanel: () => setOpen(true),
      closePanel: () => setOpen(false),
      toggle: () => setOpen((o) => !o),
      editorRef,
    }),
    [open],
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
