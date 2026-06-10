import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { Editor, type OnMount, type BeforeMount } from "@monaco-editor/react";
import { useIsDark } from "@/hooks/useIsDark";
import { activeStatement } from "./statements";

export interface SqlEditorHandle {
  // The SQL to run for the current cursor/selection: the selected text if any,
  // otherwise the single statement under the cursor.
  getRunPayload: () => string;
}

interface SqlEditorProps {
  value: string;
  onChange: (value: string) => void;
  // Invoked by the Ctrl/Cmd+Enter command with the run payload.
  onRun?: (payload: string) => void;
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
  function SqlEditor({ value, onChange, onRun, readOnly }, ref) {
    const editorRef = useRef<Parameters<OnMount>[0] | null>(null);
    const isDark = useIsDark();

    // Monaco command callbacks are captured once at mount, so route onRun
    // through a ref to always call the latest handler, not a stale closure.
    const onRunRef = useRef(onRun);
    useEffect(() => {
      onRunRef.current = onRun;
    }, [onRun]);

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

    useImperativeHandle(ref, () => ({ getRunPayload: computeRunPayload }));

    const handleBeforeMount: BeforeMount = (monaco) => {
      monaco.editor.defineTheme("duckhaven-dark", DUCKHAVEN_DARK_THEME);
      monaco.editor.defineTheme("duckhaven-light", DUCKHAVEN_LIGHT_THEME);
    };

    const handleMount: OnMount = (editor, monaco) => {
      editorRef.current = editor;

      // Ctrl+S / Cmd+S: auto-format
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        void editor.getAction("editor.action.formatDocument")?.run();
      });

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
