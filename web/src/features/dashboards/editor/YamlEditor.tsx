import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { Editor, type Monaco, type OnMount } from "@monaco-editor/react";
import { useIsDark } from "@/hooks/useIsDark";
import { defineEditorThemes } from "@/features/worksheet/editorThemes";
import type { Diagnostic } from "@/types/dashboard";
import { useDashboardYamlReady } from "./yamlLoader";

export interface YamlEditorHandle {
  /** Put the cursor where a problem is, and show it. */
  reveal: (line: number, col: number) => void;
}

interface YamlEditorProps {
  /** The model's name, `<name>.dashboard.yml`, which picks the schema. */
  path: string;
  value: string;
  onChange: (value: string) => void;
  onSave?: () => void;
  readOnly?: boolean;
  /** The JSON Schema completion and hover come from. */
  schema: object | undefined;
  /** What the server found, marked in the file. */
  errors: Diagnostic[];
  warnings: Diagnostic[];
}

/** Marks set by the server's checks, apart from any the editor sets itself. */
const OWNER = "duckhaven";

function markers(monaco: Monaco, errors: Diagnostic[], warnings: Diagnostic[]) {
  const mark = (d: Diagnostic, severity: number) => {
    const line = d.line ?? 1;
    const col = d.col ?? 1;
    return {
      severity,
      message: d.hint ? `${d.message}\n${d.hint}` : d.message,
      startLineNumber: line,
      startColumn: col,
      endLineNumber: line,
      // To the end of the line: the key or value the problem is about.
      endColumn: Number.MAX_SAFE_INTEGER,
    };
  };
  return [
    ...errors.map((d) => mark(d, monaco.MarkerSeverity.Error)),
    ...warnings.map((d) => mark(d, monaco.MarkerSeverity.Warning)),
  ];
}

/**
 * A dashboard file in Monaco: YAML with completion and hover from the
 * dashboard schema, and the server's errors and warnings marked where they
 * are. Ctrl/Cmd+S saves.
 */
export const YamlEditor = forwardRef<YamlEditorHandle, YamlEditorProps>(
  function YamlEditor(
    { path, value, onChange, onSave, readOnly, schema, errors, warnings },
    ref,
  ) {
    const ready = useDashboardYamlReady(schema);
    const isDark = useIsDark();
    const editorRef = useRef<Parameters<OnMount>[0] | null>(null);
    const monacoRef = useRef<Monaco | null>(null);
    const onSaveRef = useRef(onSave);
    useEffect(() => {
      onSaveRef.current = onSave;
    }, [onSave]);

    useImperativeHandle(ref, () => ({
      reveal: (line, col) => {
        const editor = editorRef.current;
        if (!editor) return;
        editor.setPosition({ lineNumber: line, column: col });
        editor.revealLineInCenter(line);
        editor.focus();
      },
    }));

    // The server's findings, on the model they are about.
    useEffect(() => {
      const monaco = monacoRef.current;
      const model = editorRef.current?.getModel();
      if (!monaco || !model) return;
      monaco.editor.setModelMarkers(
        model,
        OWNER,
        markers(monaco, errors, warnings),
      );
    }, [errors, warnings, ready]);

    const handleMount: OnMount = (editor, monaco) => {
      editorRef.current = editor;
      monacoRef.current = monaco;
      editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        onSaveRef.current?.();
      });
      monaco.editor.setModelMarkers(
        editor.getModel()!,
        OWNER,
        markers(monaco, errors, warnings),
      );
    };

    const loading = (
      <div className="flex h-full items-center justify-center bg-[var(--bg-canvas)] text-sm text-text-secondary">
        Loading editor…
      </div>
    );
    if (!ready) return loading;

    return (
      <Editor
        height="100%"
        language="yaml"
        path={path}
        value={value}
        onChange={(v) => onChange(v ?? "")}
        beforeMount={defineEditorThemes}
        onMount={handleMount}
        theme={isDark ? "duckhaven-dark" : "duckhaven-light"}
        loading={loading}
        options={{
          fontSize: 13,
          fontFamily: '"JetBrains Mono", Menlo, monospace',
          lineHeight: 20,
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          wordWrap: "on",
          tabSize: 2,
          insertSpaces: true,
          readOnly,
          automaticLayout: true,
          quickSuggestions: { other: true, comments: false, strings: true },
          fixedOverflowWidgets: true,
          padding: { top: 12, bottom: 12 },
        }}
      />
    );
  },
);
