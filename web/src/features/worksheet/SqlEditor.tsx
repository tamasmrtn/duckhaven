import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { Editor, type OnMount, type BeforeMount } from "@monaco-editor/react";
import { useIsDark } from "@/hooks/useIsDark";
import { activeStatement } from "./statements";
import { computeHunks } from "./diffHunks";
import { registerSqlProviders, setActiveEditor } from "./completion/provider";

// Above this many hunks, skip per-hunk view zones (still show the added-line
// decorations) — a very large AI rewrite shouldn't render dozens of view
// zones at once. Accept/reject only ever applies to the whole proposal (see
// WorksheetPage's proposal bar) — there's no per-hunk control anymore.
const MAX_INLINE_DIFF_HUNKS = 8;

export interface SqlEditorHandle {
  // The SQL to run for the current cursor/selection: the selected text if any,
  // otherwise the single statement under the cursor.
  getRunPayload: () => string;
  // The current non-empty text selection and its character offsets in the full
  // document, or null if nothing is selected. Used to scope an AI-proposed edit
  // to just the selected fragment instead of the whole worksheet.
  getSelectionRange: () => { text: string; start: number; end: number } | null;
  // Render an inline diff between an AI proposal's old and new SQL: added
  // lines are decorated in place (the document already holds the new text),
  // removed lines are shown as ghost text in a view zone above them.
  // Replaces any diff already showing.
  showDiff: (oldSql: string, newSql: string) => void;
  // Clear any inline diff rendering.
  clearDiff: () => void;
}

interface SqlEditorProps {
  value: string;
  onChange: (value: string) => void;
  // Invoked by the Ctrl/Cmd+Enter command with the run payload.
  onRun?: (payload: string) => void;
  // Invoked by the Ctrl/Cmd+S command to open the Save dialog.
  onSave?: () => void;
  readOnly?: boolean;
}

const DUCKHAVEN_DARK_THEME = {
  base: "vs-dark" as const,
  inherit: true,
  rules: [
    { token: "keyword", foreground: "FFF100", fontStyle: "bold" },
    { token: "keyword.sql", foreground: "FFF100", fontStyle: "bold" },
    { token: "string", foreground: "FF8A33" },
    { token: "string.sql", foreground: "FF8A33" },
    { token: "comment", foreground: "64748B", fontStyle: "italic" },
    { token: "number", foreground: "7DD3FC" },
    { token: "operator", foreground: "94A3B8" },
    { token: "identifier", foreground: "E2E8F0" },
    { token: "type", foreground: "7DD3FC" },
    { token: "delimiter", foreground: "64748B" },
  ],
  colors: {
    "editor.background": "#0B0F19",
    "editor.foreground": "#E2E8F0",
    "editorLineNumber.foreground": "#334155",
    "editorLineNumber.activeForeground": "#64748B",
    "editor.lineHighlightBackground": "#111827",
    "editorCursor.foreground": "#FFF100",
    "editor.selectionBackground": "#7D66FF33",
    "editor.inactiveSelectionBackground": "#7D66FF1A",
    "editorIndentGuide.background": "#1F2937",
    "editorIndentGuide.activeBackground": "#334155",
  },
};

const DUCKHAVEN_LIGHT_THEME = {
  base: "vs" as const,
  inherit: true,
  rules: [
    { token: "keyword", foreground: "C2410C", fontStyle: "bold" },
    { token: "keyword.sql", foreground: "C2410C", fontStyle: "bold" },
    { token: "string", foreground: "15803D" },
    { token: "string.sql", foreground: "15803D" },
    { token: "comment", foreground: "64748B", fontStyle: "italic" },
    { token: "number", foreground: "0369A1" },
    { token: "operator", foreground: "475569" },
    { token: "identifier", foreground: "0F172A" },
    { token: "type", foreground: "0369A1" },
    { token: "delimiter", foreground: "64748B" },
  ],
  colors: {
    "editor.background": "#FFFFFF",
    "editor.foreground": "#0F172A",
    "editorLineNumber.foreground": "#CBD5E1",
    "editorLineNumber.activeForeground": "#64748B",
    "editor.lineHighlightBackground": "#F1F5F9",
    "editorCursor.foreground": "#FF6900",
    "editor.selectionBackground": "#7D66FF26",
    "editor.inactiveSelectionBackground": "#7D66FF14",
    "editorIndentGuide.background": "#E2E8F0",
    "editorIndentGuide.activeBackground": "#CBD5E1",
  },
};

export const SqlEditor = forwardRef<SqlEditorHandle, SqlEditorProps>(
  function SqlEditor({ value, onChange, onRun, onSave, readOnly }, ref) {
    const editorRef = useRef<Parameters<OnMount>[0] | null>(null);
    const monacoRef = useRef<Parameters<OnMount>[1] | null>(null);
    const decorationsRef = useRef<string[]>([]);
    const zoneIdsRef = useRef<string[]>([]);
    const isDark = useIsDark();

    // Monaco command callbacks are captured once at mount, so route onRun/onSave
    // through refs to always call the latest handler, not a stale closure.
    const onRunRef = useRef(onRun);
    useEffect(() => {
      onRunRef.current = onRun;
    }, [onRun]);
    const onSaveRef = useRef(onSave);
    useEffect(() => {
      onSaveRef.current = onSave;
    }, [onSave]);

    // Reads the live editor: the selection if non-empty, else the single
    // statement under the cursor. Falls back to `value` only before the model
    // exists (never at runtime).
    const computeRunPayload = (): string => {
      const editor = editorRef.current;
      const model = editor?.getModel();
      if (!editor || !model) return value;
      const selection = editor.getSelection();
      const selected = selection ? model.getValueInRange(selection) : "";
      if (selected.trim()) return selected;
      const offset = model.getOffsetAt(
        editor.getPosition() ?? { lineNumber: 1, column: 1 },
      );
      return activeStatement(model.getValue(), offset);
    };

    // Removes every decoration/view-zone the inline diff added, leaving the
    // document itself untouched (it already holds whichever text is live).
    const clearDiffZones = () => {
      const editor = editorRef.current;
      if (!editor) return;
      decorationsRef.current = editor.deltaDecorations(
        decorationsRef.current,
        [],
      );
      if (zoneIdsRef.current.length > 0) {
        editor.changeViewZones((accessor) => {
          for (const id of zoneIdsRef.current) accessor.removeZone(id);
        });
        zoneIdsRef.current = [];
      }
    };

    // One removed-line ghost row inside a hunk's view zone.
    const buildRemovedLineRow = (line: string): HTMLDivElement => {
      const row = document.createElement("div");
      row.className = "dh-diff-removed-line";
      row.textContent = line.length > 0 ? line : " "; // keep empty lines visible
      return row;
    };

    useImperativeHandle(ref, () => ({
      getRunPayload: computeRunPayload,
      getSelectionRange: () => {
        const editor = editorRef.current;
        const model = editor?.getModel();
        const selection = editor?.getSelection();
        if (!editor || !model || !selection || selection.isEmpty()) return null;
        const text = model.getValueInRange(selection);
        if (!text.trim()) return null;
        const start = model.getOffsetAt({
          lineNumber: selection.startLineNumber,
          column: selection.startColumn,
        });
        const end = model.getOffsetAt({
          lineNumber: selection.endLineNumber,
          column: selection.endColumn,
        });
        return { text, start, end };
      },
      showDiff: (oldSql: string, newSql: string) => {
        const editor = editorRef.current;
        const monaco = monacoRef.current;
        if (!editor || !monaco) return;
        clearDiffZones();

        const hunks = computeHunks(oldSql, newSql);

        const decos = hunks
          .filter((h) => h.addStartLine <= h.addEndLine)
          .map((h) => ({
            range: new monaco.Range(h.addStartLine, 1, h.addEndLine, 1),
            options: {
              isWholeLine: true,
              className: "dh-diff-add-line",
              linesDecorationsClassName: "dh-diff-add-gutter",
            },
          }));
        decorationsRef.current = editor.deltaDecorations(
          decorationsRef.current,
          decos,
        );

        // A very large AI rewrite could otherwise produce dozens of view
        // zones at once — cap the removed-line ghost text and rely on the
        // proposal bar's whole-file Accept/Reject beyond that. The green
        // added-line decorations above still show what changed either way.
        const hunksForZones = hunks
          .filter((h) => h.removedLines.length > 0)
          .slice(0, MAX_INLINE_DIFF_HUNKS);

        editor.changeViewZones((accessor) => {
          const zoneIds: string[] = [];
          for (const hunk of hunksForZones) {
            const domNode = document.createElement("div");
            domNode.className = "dh-diff-zone";
            for (const line of hunk.removedLines) {
              domNode.append(buildRemovedLineRow(line));
            }

            zoneIds.push(
              accessor.addZone({
                afterLineNumber: hunk.addStartLine - 1,
                heightInLines: hunk.removedLines.length,
                domNode,
              }),
            );
          }
          zoneIdsRef.current = zoneIds;
        });
      },
      clearDiff: clearDiffZones,
    }));

    const handleBeforeMount: BeforeMount = (monaco) => {
      monaco.editor.defineTheme("duckhaven-dark", DUCKHAVEN_DARK_THEME);
      monaco.editor.defineTheme("duckhaven-light", DUCKHAVEN_LIGHT_THEME);
      // Catalog- and DuckDB-aware completions + signature help. Registered once
      // for the language; reads its data through a module-level context ref.
      registerSqlProviders(monaco);
    };

    const handleMount: OnMount = (editor, monaco) => {
      editorRef.current = editor;
      monacoRef.current = monaco;
      // Let the completion data layer refresh an open suggest widget once
      // lazily-fetched columns arrive.
      setActiveEditor(editor);

      // Ctrl+S / Cmd+S: open the Save dialog (the muscle-memory "save"). Format
      // moves to Monaco's standard Shift+Alt+F.
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        onSaveRef.current?.();
      });
      editor.addCommand(
        monaco.KeyMod.Shift | monaco.KeyMod.Alt | monaco.KeyCode.KeyF,
        () => {
          void editor.getAction("editor.action.formatDocument")?.run();
        },
      );

      // Ctrl+Enter / Cmd+Enter: run. A distinct chord from plain Enter, so the
      // suggestion widget (which consumes Enter) does not swallow it.
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => {
        onRunRef.current?.(computeRunPayload());
      });

      // Dropping a table from the catalog sidebar inserts its fully-qualified
      // name at the drop position, instead of the sidebar's click handler
      // overwriting the whole tab (see CatalogTree's draggable table rows).
      const domNode = editor.getDomNode();
      domNode?.addEventListener("dragover", (e) => {
        e.preventDefault();
      });
      domNode?.addEventListener("drop", (e) => {
        e.preventDefault();
        const text = e.dataTransfer?.getData("text/plain");
        if (!text) return;
        const target = editor.getTargetAtClientPoint(e.clientX, e.clientY);
        const position = target?.position ?? editor.getPosition();
        if (!position) return;
        editor.executeEdits("drag-drop-table", [
          {
            range: new monaco.Range(
              position.lineNumber,
              position.column,
              position.lineNumber,
              position.column,
            ),
            text,
          },
        ]);
        editor.focus();
      });
    };

    return (
      <Editor
        height="100%"
        defaultLanguage="sql"
        value={value}
        onChange={(v) => onChange(v ?? "")}
        beforeMount={handleBeforeMount}
        onMount={handleMount}
        theme={isDark ? "duckhaven-dark" : "duckhaven-light"}
        options={{
          fontSize: 13,
          fontFamily: '"JetBrains Mono", Menlo, monospace',
          fontLigatures: true,
          lineHeight: 20,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          wordWrap: "on",
          tabSize: 2,
          lineNumbers: "on",
          glyphMargin: true,
          folding: false,
          readOnly,
          automaticLayout: true,
          suggest: { showKeywords: true },
          quickSuggestions: true,
          // Our provider supplies catalog/function-aware suggestions; turn off
          // Monaco's generic word-based ones so they don't add noise.
          wordBasedSuggestions: "off",
          // Render the suggest/parameter-hint overflow widgets in the document
          // body so the detail flyout isn't clipped by the editor container or
          // the catalog sidebar.
          fixedOverflowWidgets: true,
          padding: { top: 12, bottom: 12 },
          // Monaco's own built-in drop handling would otherwise fire alongside
          // the custom "drop" listener registered in handleMount, inserting
          // the dropped table name a second time (as a snippet, leaking a
          // literal "$0" final-tabstop marker into the text).
          dropIntoEditor: { enabled: false },
        }}
        loading={
          <div className="flex h-full items-center justify-center bg-[var(--bg-canvas)] text-text-secondary text-sm">
            Loading editor…
          </div>
        }
      />
    );
  },
);
