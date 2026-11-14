import { useEffect, useState } from "react";
import { loadMonaco } from "@/features/worksheet/monacoLoader";

// The YAML language rides on the SQL editor's bundled Monaco, so it loads
// that first, then its own chunk.
let loading: Promise<void> | undefined;
let loaded = false;

export function loadDashboardYaml(schema: object): Promise<void> {
  loading ??= loadMonaco()
    .then(() => import("./yamlBundle"))
    .then(({ configureDashboardYaml }) => {
      configureDashboardYaml(schema);
      loaded = true;
    });
  return loading;
}

/** True once the editor can open a dashboard file with its schema. */
export function useDashboardYamlReady(schema: object | undefined): boolean {
  const [ready, setReady] = useState(loaded);
  useEffect(() => {
    if (ready || !schema) return;
    let cancelled = false;
    loadDashboardYaml(schema).then(
      () => {
        if (!cancelled) setReady(true);
      },
      (err: unknown) => {
        // Let a later editor try again.
        loading = undefined;
        console.error("Failed to load the dashboard editor", err);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [ready, schema]);
  return ready;
}
