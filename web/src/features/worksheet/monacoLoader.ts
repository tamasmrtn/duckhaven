import { useEffect, useState } from "react";
import { loader } from "@monaco-editor/react";

// @monaco-editor/react fetches Monaco from cdn.jsdelivr.net at runtime unless
// it is handed an instance first. Giving it our own bundled copy keeps the
// editor working without internet access and makes package-lock.json the
// whole truth about the code that runs in the browser.
let loading: Promise<void> | undefined;
let loaded = false;

export function loadMonaco(): Promise<void> {
  loading ??= import("./monacoBundle").then(({ monaco }) => {
    loader.config({ monaco });
    loaded = true;
  });
  return loading;
}

// True once Monaco is configured, so <Editor> can mount without falling back
// to the CDN.
export function useMonacoReady(): boolean {
  const [ready, setReady] = useState(loaded);
  useEffect(() => {
    if (ready) return;
    let cancelled = false;
    loadMonaco().then(
      () => {
        if (!cancelled) setReady(true);
      },
      (err: unknown) => console.error("Failed to load the SQL editor", err),
    );
    return () => {
      cancelled = true;
    };
  }, [ready]);
  return ready;
}
