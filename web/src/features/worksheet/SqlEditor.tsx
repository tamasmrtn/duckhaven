import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { Editor, type OnMount, type BeforeMount } from "@monaco-editor/react";
import { useIsDark } from "@/hooks/useIsDark";
import { activeStatement } from "./statements";
import { registerSqlProviders, setActiveEditor } from "./completion/provider";

export interface SqlEditorHandle {
  // The SQL to run for the current cursor/selection: the selected text if any,
  // otherwise the single statement under the cursor.
  getRunPayload: () => string;
  // Highlight the lines that differ between the previous and proposed SQL, so an
  // AI-proposed edit is visually distinct from the user's own code.
  highlightDiff: (oldSql: string, newSql: string) => void;
  // Clear any AI-diff highlighting.
  clearHighlight: () => void;
}

// Naive line-level diff: the 1-based line numbers in `newSql` that differ from
// `oldSql` at the same position (or are new). Enough to visually flag changes.
function changedLineNumbers(oldSql: string, newSql: string): number[] {
  const oldLines = oldSql.split("\n");
  const newLines = newSql.split("\n");
  const changed: number[] = [];
  for (let i = 0; i < newLines.length; i++) {
    if (oldLines[i] !== newLines[i]) changed.push(i + 1);
  }
  return changed;
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

    useImperativeHandle(ref, () => ({
      getRunPayload: computeRunPayload,
      highlightDiff: (oldSql: string, newSql: string) => {
        const editor = editorRef.current;
        const monaco = monacoRef.current;
        if (!editor || !monaco) return;
        const decos = changedLineNumbers(oldSql, newSql).map((ln) => ({
          range: new monaco.Range(ln, 1, ln, 1),
          options: {
            isWholeLine: true,
            className: "dh-ai-diff-line",
            linesDecorationsClassName: "dh-ai-diff-gutter",
          },
        }));
        decorationsRef.current = editor.deltaDecorations(
          decorationsRef.current,
          decos,
        );
      },
      clearHighlight: () => {
        const editor = editorRef.current;
        if (editor) {
          decorationsRef.current = editor.deltaDecorations(
            decorationsRef.current,
            [],
          );
        }
      },
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
