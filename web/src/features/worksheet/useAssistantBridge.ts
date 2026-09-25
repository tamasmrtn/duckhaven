import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import { useAssistant } from "@/features/assistant/AssistantContext";
import type { SqlEditorHandle } from "./SqlEditor";
import { applyScopedEdit } from "./scopedEdit";

export interface Proposal {
  worksheetId: string;
  oldSql: string;
  newSql: string;
  explanation: string;
  note?: string;
}

/**
 * The assistant ↔ editor bridge: lets the AI panel read the active worksheet's
 * SQL and propose edits the user accepts or rejects, with the changed lines
 * highlighted in the editor.
 */
export function useAssistantBridge({
  activeId,
  sql,
  catalog,
  editorRef,
  applySql,
}: {
  activeId: string | undefined;
  sql: string;
  catalog: string | undefined;
  editorRef: RefObject<SqlEditorHandle | null>;
  applySql: (worksheetId: string, sql: string) => void;
}) {
  const { editorRef: assistantEditorRef, openPanel } = useAssistant();
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const sqlRef = useRef(sql);
  const catalogRef = useRef(catalog);
  const activeRef = useRef(activeId);
  // The selection last read by the assistant (at send() time), so a scoped
  // propose_edit response can be spliced back into the same range.
  const lastSelectionRef = useRef<{
    text: string;
    start: number;
    end: number;
  } | null>(null);
  const applyRef = useRef(applySql);
  // The bridge is registered once and reads the latest values through these.
  useLayoutEffect(() => {
    sqlRef.current = sql;
    catalogRef.current = catalog;
    activeRef.current = activeId;
    applyRef.current = applySql;
  });

  // Register the bridge once; it reads the latest values through refs.
  useEffect(() => {
    assistantEditorRef.current = {
      getSql: () => sqlRef.current,
      proposeEdit: (newSql, explanation, scoped) => {
        const id = activeRef.current;
        if (!id) return;
        const oldSql = sqlRef.current;
        const applied = applyScopedEdit(
          oldSql,
          lastSelectionRef.current,
          newSql,
          scoped,
        );
        setProposal({
          worksheetId: id,
          oldSql,
          newSql: applied.sql,
          explanation,
          note: applied.note,
        });
        applyRef.current(id, applied.sql);
      },
      getCatalog: () => catalogRef.current ?? null,
      captureSelection: () => {
        const sel = editorRef.current?.getSelectionRange() ?? null;
        lastSelectionRef.current = sel;
        return sel;
      },
    };
    return () => {
      assistantEditorRef.current = null;
    };
  }, [assistantEditorRef, editorRef]);

  // Render the inline diff once the editor reflects the new SQL.
  useEffect(() => {
    if (proposal && proposal.worksheetId === activeId) {
      editorRef.current?.showDiff(proposal.oldSql, proposal.newSql);
    }
  }, [proposal, activeId, editorRef]);

  function accept() {
    editorRef.current?.clearDiff();
    setProposal(null);
  }

  function reject() {
    if (proposal) applyRef.current(proposal.worksheetId, proposal.oldSql);
    editorRef.current?.clearDiff();
    setProposal(null);
  }

  return {
    proposal: proposal && proposal.worksheetId === activeId ? proposal : null,
    accept,
    reject,
    openPanel,
  };
}
