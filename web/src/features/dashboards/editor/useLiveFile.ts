import { useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";
import { dashboardsApi } from "@/api/dashboards";
import type {
  DashboardPreview,
  DashboardValidation,
  Diagnostic,
} from "@/types/dashboard";

/** How long typing pauses before the file is checked and the preview updated. */
export const SETTLE_MS = 700;

export interface LiveFile {
  /** The last file that loaded, held on the server and run as the preview. */
  preview: DashboardPreview | null;
  /** What checking the current file the way publishing does found. */
  errors: Diagnostic[];
  warnings: Diagnostic[];
  /** Whether `preview` is the file as it stands. */
  current: boolean;
  /** Whether the file as it stands does not load, so `preview` is an older one. */
  refused: boolean;
  checking: boolean;
  /** Why the preview could not be updated, when the file is not to blame. */
  failure: string | null;
}

const NONE: Diagnostic[] = [];

function diagnostics(err: unknown): Diagnostic[] | null {
  if (!(err instanceof ApiError) || err.status !== 422) return null;
  const list = err.details?.diagnostics;
  return Array.isArray(list) ? (list as Diagnostic[]) : null;
}

/**
 * Follow the file being edited: once typing pauses, check it as publishing
 * would and hold it in a preview to run. A file that does not load keeps the
 * last preview that did, so the page does not empty while someone types.
 */
export function useLiveFile(ws: string, text: string): LiveFile {
  const [preview, setPreview] = useState<DashboardPreview | null>(null);
  const [checked, setChecked] = useState<{
    text: string;
    result: DashboardValidation | null;
  } | null>(null);
  const [held, setHeld] = useState<string | null>(null);
  const [refusedText, setRefused] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  // The preview's id outlives renders; a stale one is replaced with a new one.
  const id = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      const check = dashboardsApi.validate(ws, text).then(
        (result) => !cancelled && setChecked({ text, result }),
        () => !cancelled && setChecked({ text, result: null }),
      );
      const hold = (async () => {
        try {
          let next: DashboardPreview;
          try {
            next = id.current
              ? await dashboardsApi.replacePreview(ws, id.current, text)
              : await dashboardsApi.createPreview(ws, text);
          } catch (err) {
            // Two hours unused and it is gone: start another.
            if (!(err instanceof ApiError && err.status === 404)) throw err;
            next = await dashboardsApi.createPreview(ws, text);
          }
          if (cancelled) return;
          id.current = next.id;
          setPreview(next);
          setHeld(text);
          setFailure(null);
        } catch (err) {
          if (cancelled) return;
          if (diagnostics(err)) {
            setRefused(text);
            return;
          }
          setFailure(
            err instanceof Error
              ? err.message
              : "The preview could not be updated.",
          );
        }
      })();
      void Promise.all([check, hold]);
    }, SETTLE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [ws, text]);

  const result = checked?.result ?? null;
  return {
    preview,
    errors: result?.errors ?? NONE,
    warnings: result?.warnings ?? NONE,
    current: held === text,
    refused: refusedText === text,
    checking: checked?.text !== text,
    failure,
  };
}
