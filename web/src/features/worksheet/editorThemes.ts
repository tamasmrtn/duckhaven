import type { Monaco } from "@monaco-editor/react";

// The editors' colours, light and dark, shared by the SQL editor and the
// dashboard editor.
const DARK = {
  base: "vs-dark" as const,
  inherit: true,
  rules: [
    { token: "keyword", foreground: "FFF100", fontStyle: "bold" },
    { token: "keyword.sql", foreground: "FFF100", fontStyle: "bold" },
    { token: "string", foreground: "FF8A33" },
    { token: "string.sql", foreground: "FF8A33" },
    { token: "comment", foreground: "8B9BB0", fontStyle: "italic" },
    { token: "number", foreground: "7DD3FC" },
    { token: "operator", foreground: "94A3B8" },
    // The base theme's own `operator.sql` is more specific than `operator`,
    // so without this it wins: #778899.
    { token: "operator.sql", foreground: "94A3B8" },
    { token: "identifier", foreground: "E2E8F0" },
    { token: "type", foreground: "7DD3FC" },
    { token: "delimiter", foreground: "8B9BB0" },
  ],
  colors: {
    "editor.background": "#0B0F19",
    "editor.foreground": "#E2E8F0",
    // Line numbers are read: the problems list under the dashboard editor
    // points at them. Each stays at 4.5:1 on the background.
    "editorLineNumber.foreground": "#8B9BB0",
    "editorLineNumber.activeForeground": "#E2E8F0",
    "editor.lineHighlightBackground": "#111827",
    "editorCursor.foreground": "#FFF100",
    "editor.selectionBackground": "#7D66FF33",
    "editor.inactiveSelectionBackground": "#7D66FF1A",
    "editorIndentGuide.background": "#1F2937",
    "editorIndentGuide.activeBackground": "#334155",
  },
};

const LIGHT = {
  base: "vs" as const,
  inherit: true,
  rules: [
    { token: "keyword", foreground: "C2410C", fontStyle: "bold" },
    { token: "keyword.sql", foreground: "C2410C", fontStyle: "bold" },
    { token: "string", foreground: "15803D" },
    { token: "string.sql", foreground: "15803D" },
    { token: "comment", foreground: "5B6B82", fontStyle: "italic" },
    { token: "number", foreground: "0369A1" },
    { token: "operator", foreground: "475569" },
    { token: "operator.sql", foreground: "475569" },
    { token: "identifier", foreground: "0F172A" },
    { token: "type", foreground: "0369A1" },
    { token: "delimiter", foreground: "5B6B82" },
  ],
  colors: {
    "editor.background": "#FFFFFF",
    "editor.foreground": "#0F172A",
    "editorLineNumber.foreground": "#64748B",
    "editorLineNumber.activeForeground": "#0F172A",
    "editor.lineHighlightBackground": "#F1F5F9",
    "editorCursor.foreground": "#FF6900",
    "editor.selectionBackground": "#7D66FF26",
    "editor.inactiveSelectionBackground": "#7D66FF14",
    "editorIndentGuide.background": "#E2E8F0",
    "editorIndentGuide.activeBackground": "#CBD5E1",
  },
};

/** Define `duckhaven-dark` and `duckhaven-light` for an editor to use. */
export function defineEditorThemes(monaco: Monaco): void {
  monaco.editor.defineTheme("duckhaven-dark", DARK);
  monaco.editor.defineTheme("duckhaven-light", LIGHT);
}
