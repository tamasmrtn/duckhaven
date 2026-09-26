import { useCallback, useState } from "react";

export interface DispatchError {
  message: string;
  // The API's machine code: `unavailable` means the agent could not be reached.
  code?: string;
}

/** What a tab is showing right now. Not persisted except through `last_query_id`. */
export interface TabRuntime {
  // Undefined until this page runs something in the tab, so the worksheet's
  // saved `last_query_id` shows; null once a run has cleared it.
  queryId: string | null | undefined;
  runSeq: { index: number; total: number } | null;
  dispatchError: DispatchError | null;
  resultsTab: "results" | "profile";
}

const EMPTY: TabRuntime = {
  queryId: undefined,
  runSeq: null,
  dispatchError: null,
  resultsTab: "results",
};

/**
 * Results, progress and errors per worksheet, so a new tab starts empty and a
 * closed tab's results never show up in the tab that replaces it.
 */
export function useWorksheetRuntime() {
  const [byId, setById] = useState<Record<string, TabRuntime>>({});

  const get = useCallback(
    (id: string | undefined): TabRuntime => (id && byId[id]) || EMPTY,
    [byId],
  );

  const update = useCallback((id: string, patch: Partial<TabRuntime>) => {
    setById((prev) => ({ ...prev, [id]: { ...EMPTY, ...prev[id], ...patch } }));
  }, []);

  const drop = useCallback((id: string) => {
    setById((prev) => {
      if (!(id in prev)) return prev;
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }, []);

  return { get, update, drop };
}

export type WorksheetRuntime = ReturnType<typeof useWorksheetRuntime>;
