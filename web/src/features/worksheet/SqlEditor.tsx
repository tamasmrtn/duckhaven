import { useRef } from "react";
import { Editor, type OnMount } from "@monaco-editor/react";

interface SqlEditorProps {
  value: string;
  onChange: (value: string) => void;
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

export function SqlEditor({ value, onChange, readOnly }: SqlEditorProps) {
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null);

  const handleMount: OnMount = (editor, monaco) => {
    editorRef.current = editor;

    monaco.editor.defineTheme("duckhaven-dark", DUCKHAVEN_DARK_THEME);
    monaco.editor.setTheme("duckhaven-dark");

    // Ctrl+S / Cmd+S: auto-format
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      void editor.getAction("editor.action.formatDocument")?.run();
    });
  };

  return (
    <Editor
      height="100%"
      defaultLanguage="sql"
      value={value}
      onChange={(v) => onChange(v ?? "")}
      onMount={handleMount}
      theme="duckhaven-dark"
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
        <div className="flex h-full items-center justify-center bg-[var(--bg-code)] text-text-secondary text-sm">
          Loading editor…
        </div>
      }
    />
  );
}
