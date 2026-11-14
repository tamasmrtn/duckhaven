import { useCallback, useEffect, useReducer } from "react";
import { ApiError } from "@/api/client";
import { dashboardsApi } from "@/api/dashboards";
import type {
  DashboardRunRequest,
  RunDone,
  RunEvent,
  RunStart,
  RunWidget,
} from "@/types/dashboard";

/** One widget's place on the page: its latest answer, and whether a run is answering it now. */
export interface WidgetSlot {
  event: RunWidget | null;
  /** A run is under way for it. With an `event`, what shows is stale. */
  pending: boolean;
}

export interface RunView {
  running: boolean;
  start: RunStart | null;
  widgets: Record<string, WidgetSlot>;
  /** The widgets waiting for compute to start. */
  waiting: string[];
  done: RunDone | null;
  /** Why the view could not run, or stopped. */
  error: { message: string; code?: string } | null;
}

type Action =
  | { kind: "begin" }
  | { kind: "event"; event: RunEvent }
  | { kind: "fail"; message: string; code?: string }
  | { kind: "closed" };

const INITIAL: RunView = {
  running: false,
  start: null,
  widgets: {},
  waiting: [],
  done: null,
  error: null,
};

function settle(widgets: Record<string, WidgetSlot>) {
  return Object.fromEntries(
    Object.entries(widgets).map(([id, slot]) => [
      id,
      { ...slot, pending: false },
    ]),
  );
}

export function reduce(view: RunView, action: Action): RunView {
  switch (action.kind) {
    case "begin":
      // What the last run answered stays on screen, marked stale, until the
      // new run answers it again.
      return {
        ...INITIAL,
        running: true,
        widgets: Object.fromEntries(
          Object.entries(view.widgets).map(([id, slot]) => [
            id,
            { ...slot, pending: true },
          ]),
        ),
      };
    case "event": {
      const event = action.event;
      switch (event.type) {
        case "start": {
          const widgets: Record<string, WidgetSlot> = {};
          for (const id of event.widgets) {
            widgets[id] = {
              event: view.widgets[id]?.event ?? null,
              pending: true,
            };
          }
          return { ...view, start: event, widgets };
        }
        case "compute":
          return { ...view, waiting: event.widgets };
        case "widget":
          return {
            ...view,
            widgets: {
              ...view.widgets,
              [event.id]: { event, pending: false },
            },
            waiting: view.waiting.filter((id) => id !== event.id),
          };
        case "done":
          return { ...view, running: false, done: event, waiting: [] };
        case "error":
          return {
            ...view,
            running: false,
            waiting: [],
            widgets: settle(view.widgets),
            error: { message: event.message },
          };
      }
      return view;
    }
    case "fail":
      return {
        ...view,
        running: false,
        waiting: [],
        widgets: settle(view.widgets),
        error: { message: action.message, code: action.code },
      };
    case "closed":
      if (!view.running) return view;
      // The stream ended without saying it was done: the connection dropped.
      return {
        ...view,
        running: false,
        waiting: [],
        widgets: settle(view.widgets),
        error: {
          message:
            "The connection closed before every widget was answered. Refresh to try again.",
        },
      };
  }
}

/** A preview to run, by id, and the hash of the file it holds. */
export interface PreviewTarget {
  preview: string;
  hash: string;
}

/**
 * Run a dashboard, by name, or a preview as the signed-in person and follow
 * its event stream.
 *
 * It runs when the page opens and again whenever `request` changes, or a
 * preview's file does; `refresh` runs it once more. Leaving the page, or
 * starting another run, closes the stream, which cancels the server's queries
 * still running.
 */
export function useDashboardRun(
  ws: string,
  target: string | PreviewTarget | null,
  request: DashboardRunRequest,
  enabled = true,
) {
  const [view, dispatch] = useReducer(reduce, INITIAL);
  const [generation, rerun] = useReducer((n: number) => n + 1, 0);
  const body = JSON.stringify(request);
  const name = typeof target === "string" ? target : null;
  const preview = target && typeof target !== "string" ? target.preview : null;
  const hash = target && typeof target !== "string" ? target.hash : null;

  useEffect(() => {
    if (!enabled || !ws || !(name || preview)) return;
    const abort = new AbortController();
    const request = JSON.parse(body) as DashboardRunRequest;
    dispatch({ kind: "begin" });
    void (async () => {
      try {
        for await (const event of preview
          ? dashboardsApi.runPreview(ws, preview, request, abort.signal)
          : dashboardsApi.run(ws, name!, request, abort.signal)) {
          dispatch({ kind: "event", event });
        }
        if (!abort.signal.aborted) dispatch({ kind: "closed" });
      } catch (err) {
        if (abort.signal.aborted) return;
        dispatch(
          err instanceof ApiError
            ? { kind: "fail", message: err.message, code: err.code }
            : { kind: "fail", message: "The dashboard could not be run." },
        );
      }
    })();
    return () => abort.abort();
  }, [ws, name, preview, hash, body, enabled, generation]);

  const refresh = useCallback(() => rerun(), []);
  return { ...view, refresh };
}
