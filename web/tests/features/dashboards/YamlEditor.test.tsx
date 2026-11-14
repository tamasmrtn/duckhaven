import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import {
  YamlEditor,
  type YamlEditorHandle,
} from "@/features/dashboards/editor/YamlEditor";

const { editor, monaco, mounted } = vi.hoisted(() => {
  const commands: Record<number, () => void> = {};
  const model = { uri: "file:///sales.dashboard.yml" };
  const editor = {
    commands,
    getModel: () => model,
    addCommand: (key: number, run: () => void) => {
      commands[key] = run;
    },
    setPosition: vi.fn(),
    revealLineInCenter: vi.fn(),
    focus: vi.fn(),
  };
  const monaco = {
    KeyMod: { CtrlCmd: 2048 },
    KeyCode: { KeyS: 49 },
    MarkerSeverity: { Error: 8, Warning: 4 },
    editor: { setModelMarkers: vi.fn(), defineTheme: vi.fn() },
  };
  return { editor, monaco, mounted: { path: "" } };
});

// Mount at once with a stand-in editor, as Monaco would once loaded.
vi.mock("@monaco-editor/react", async () => {
  const { useEffect } = await import("react");
  return {
    Editor: ({
      path,
      onMount,
    }: {
      path: string;
      onMount: (e: unknown, m: unknown) => void;
    }) => {
      useEffect(() => {
        mounted.path = path;
        onMount(editor, monaco);
      }, []); // eslint-disable-line react-hooks/exhaustive-deps
      return null;
    },
  };
});
vi.mock("@/features/dashboards/editor/yamlLoader", () => ({
  useDashboardYamlReady: () => true,
}));

const ERROR = {
  path: "rows[0].widgets[0].query",
  line: 7,
  col: 9,
  message: "No metric revenue_net in sales.",
  hint: "Did you mean revenue?",
};
const WARNING = { path: "", line: null, col: null, message: "17 widgets." };

describe("YamlEditor", () => {
  it("marks the server's errors and warnings where they are", () => {
    const { rerender } = render(
      <YamlEditor
        path="sales.dashboard.yml"
        value=""
        onChange={() => {}}
        schema={{}}
        errors={[ERROR]}
        warnings={[WARNING]}
      />,
    );

    expect(mounted.path).toBe("sales.dashboard.yml");
    const [model, owner, markers] =
      monaco.editor.setModelMarkers.mock.calls.at(-1)!;
    expect(model).toBe(editor.getModel());
    expect(owner).toBe("duckhaven");
    expect(markers).toEqual([
      expect.objectContaining({
        severity: 8,
        startLineNumber: 7,
        startColumn: 9,
        message: "No metric revenue_net in sales.\nDid you mean revenue?",
      }),
      // A finding about the whole file is marked on its first line.
      expect.objectContaining({ severity: 4, startLineNumber: 1, startColumn: 1 }),
    ]);

    rerender(
      <YamlEditor
        path="sales.dashboard.yml"
        value=""
        onChange={() => {}}
        schema={{}}
        errors={[]}
        warnings={[]}
      />,
    );
    expect(monaco.editor.setModelMarkers.mock.calls.at(-1)![2]).toEqual([]);
  });

  it("saves on Ctrl+S and shows where a problem is", () => {
    const onSave = vi.fn();
    const ref = createRef<YamlEditorHandle>();
    render(
      <YamlEditor
        ref={ref}
        path="sales.dashboard.yml"
        value=""
        onChange={() => {}}
        onSave={onSave}
        schema={{}}
        errors={[]}
        warnings={[]}
      />,
    );

    editor.commands[2048 | 49]();
    expect(onSave).toHaveBeenCalledOnce();

    ref.current!.reveal(7, 9);
    expect(editor.setPosition).toHaveBeenCalledWith({ lineNumber: 7, column: 9 });
    expect(editor.revealLineInCenter).toHaveBeenCalledWith(7);
    expect(editor.focus).toHaveBeenCalled();
  });
});
