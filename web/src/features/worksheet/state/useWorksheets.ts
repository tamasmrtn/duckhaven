import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { worksheetsApi } from "@/api/worksheets";
import { queriesApi } from "@/api/queries";
import { openWorksheetsKey, worksheetsKey } from "@/queries/worksheets";
import type {
  Worksheet,
  WorksheetContent,
  WorksheetCreate,
  WorksheetMeta,
} from "@/types/worksheet";
import { hasLegacyTabs, migrateLegacyTabs } from "./legacyTabs";
import { WorksheetSync, type SyncStatus } from "./worksheetSync";

const activeTabKey = (ws: string) => `dh-active-tab-${ws}`;

function loadActiveTab(ws: string): string | null {
  try {
    return sessionStorage.getItem(activeTabKey(ws));
  } catch {
    return null;
  }
}

function storeActiveTab(ws: string, id: string): void {
  try {
    sessionStorage.setItem(activeTabKey(ws), id);
  } catch {
    // Only the tab a new visit lands on is lost.
  }
}

/**
 * A new worksheet is named for when it was made (Snowsight's convention), so a
 * row of fresh tabs is never a row of identical "untitled"s.
 */
export function timestampTitle(now = new Date()): string {
  return now.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** The open tabs, first uploading this browser's legacy tabs and never returning none. */
async function bootstrapOpenWorksheets(
  ws: string,
  qc: QueryClient,
): Promise<Worksheet[]> {
  let open = await worksheetsApi.listOpen(ws);
  if (hasLegacyTabs(ws)) {
    const saved = await qc.fetchQuery({
      queryKey: ["workspace", ws, "saved-queries"],
      queryFn: () => queriesApi.listSaved(ws),
    });
    const { created, activeId } = await migrateLegacyTabs(ws, {
      create: (body) => worksheetsApi.create(ws, body),
      savedQueryIds: new Set(saved.map((s) => s.id)),
    });
    open = [...open, ...created];
    if (activeId) storeActiveTab(ws, activeId);
  }
  if (open.length === 0) {
    open = [await worksheetsApi.create(ws, { title: timestampTitle() })];
  }
  return open;
}

export interface WorksheetsState {
  worksheets: Worksheet[];
  isLoading: boolean;
  active: Worksheet | undefined;
  select: (id: string) => void;
  create: (body?: WorksheetCreate) => Promise<Worksheet | undefined>;
  open: (id: string) => Promise<void>;
  close: (id: string) => Promise<void>;
  rename: (id: string, title: string) => void;
  remove: (id: string) => Promise<void>;
  edit: (id: string, fields: WorksheetContent) => void;
  setMeta: (id: string, meta: WorksheetMeta) => void;
  flush: (id: string) => Promise<void>;
  status: Record<string, SyncStatus>;
  conflicts: Record<string, Worksheet>;
  loadTheirs: (id: string) => void;
  keepMine: (id: string) => void;
}

/**
 * The worksheet tabs of `ws`: server-side, autosaved, restored on any device.
 *
 * `requestedTab` is the `?tab=` search param. A tab closed earlier is reopened
 * when named there, which is how a deep link or the worksheet browser opens one.
 */
export function useWorksheets(
  ws: string,
  requestedTab: string | undefined,
  onClosed?: (id: string) => void,
): WorksheetsState {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const key = useMemo(() => openWorksheetsKey(ws), [ws]);
  const query = useQuery({
    queryKey: key,
    queryFn: () => bootstrapOpenWorksheets(ws, qc),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const worksheets = useMemo(() => query.data ?? [], [query.data]);
  // Callbacks read the latest list through this rather than re-binding on
  // every edit.
  const worksheetsRef = useRef(worksheets);
  useLayoutEffect(() => {
    worksheetsRef.current = worksheets;
  });

  const [status, setStatus] = useState<Record<string, SyncStatus>>({});
  const [conflicts, setConflicts] = useState<Record<string, Worksheet>>({});
  const [activeId, setActiveId] = useState<string | null>(
    () => requestedTab ?? loadActiveTab(ws),
  );

  const patchCache = useCallback(
    (id: string, fields: Partial<Worksheet>) => {
      qc.setQueryData<Worksheet[]>(key, (list) =>
        list?.map((w) => (w.id === id ? { ...w, ...fields } : w)),
      );
    },
    [qc, key],
  );
  const dropFromCache = useCallback(
    (id: string) => {
      qc.setQueryData<Worksheet[]>(key, (list) =>
        list?.filter((w) => w.id !== id),
      );
    },
    [qc, key],
  );
  const refreshBrowser = useCallback(() => {
    void qc.invalidateQueries({ queryKey: [...worksheetsKey(ws), "browse"] });
  }, [qc, ws]);

  const sync = useMemo(
    () =>
      new WorksheetSync({
        save: (id, body) => worksheetsApi.update(ws, id, body),
        saved: (id, saved) =>
          patchCache(id, {
            version: saved.version,
            updated_at: saved.updated_at,
          }),
        status: (id, s) => setStatus((prev) => ({ ...prev, [id]: s })),
        conflict: (id, current) =>
          setConflicts((prev) => ({ ...prev, [id]: current })),
        deleted: (id) => {
          dropFromCache(id);
          toast.error("That worksheet was deleted in another window.");
        },
      }),
    [ws, patchCache, dropFromCache],
  );

  // The last autosave goes out even if the route unmounts or the tab closes.
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === "hidden") void sync.flushAll();
      else void mergeFromServer();
    };
    const onPageHide = () => {
      for (const { id, body } of sync.unloadBodies().bodies) {
        worksheetsApi.updateKeepalive(ws, id, body);
      }
    };
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (sync.unloadBodies().unsendable) e.preventDefault();
    };
    // Picks up what another window saved while this one was in the background.
    async function mergeFromServer() {
      let server: Worksheet[];
      try {
        server = await worksheetsApi.listOpen(ws);
      } catch {
        return;
      }
      qc.setQueryData<Worksheet[]>(key, (list = []) => {
        const byId = new Map(list.map((w) => [w.id, w]));
        const merged = server.map((s) => {
          const local = byId.get(s.id);
          if (!local || sync.hasPending(s.id)) return local ?? s;
          return s.version > local.version ? s : local;
        });
        // A tab closed elsewhere stays if it still has unsaved edits here.
        const kept = list.filter(
          (w) => !server.some((s) => s.id === w.id) && sync.hasPending(w.id),
        );
        return [...merged, ...kept];
      });
    }
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("beforeunload", onBeforeUnload);
      void sync.flushAll().finally(() => sync.dispose());
    };
  }, [sync, ws, qc, key]);

  const active =
    worksheets.find((w) => w.id === activeId) ?? worksheets[0] ?? undefined;

  const select = useCallback(
    (id: string) => {
      const previous = activeId;
      setActiveId(id);
      storeActiveTab(ws, id);
      if (previous && previous !== id) void sync.flush(previous);
      void navigate({
        to: "/$ws/worksheets",
        params: { ws },
        search: (prev) => ({ ...prev, tab: id }),
        replace: true,
      });
    },
    [activeId, ws, sync, navigate],
  );

  const appendOpen = useCallback(
    (sheet: Worksheet) => {
      qc.setQueryData<Worksheet[]>(key, (list = []) =>
        list.some((w) => w.id === sheet.id)
          ? list.map((w) => (w.id === sheet.id ? sheet : w))
          : [...list, sheet],
      );
    },
    [qc, key],
  );

  // `?tab=` naming a worksheet that is not an open tab: reopen it if it is ours.
  const loaded = query.isSuccess;
  useEffect(() => {
    if (!loaded || !requestedTab) return;
    if (worksheetsRef.current.some((w) => w.id === requestedTab)) {
      setActiveId(requestedTab);
      storeActiveTab(ws, requestedTab);
      return;
    }
    let cancelled = false;
    worksheetsApi
      .get(ws, requestedTab)
      .then((sheet) =>
        sheet.is_open
          ? sheet
          : worksheetsApi.update(ws, sheet.id, { is_open: true }),
      )
      .then((sheet) => {
        if (cancelled) return;
        appendOpen(sheet);
        setActiveId(sheet.id);
        storeActiveTab(ws, sheet.id);
      })
      .catch(() => {
        // Not ours, or gone: stay on the tab already showing.
      });
    return () => {
      cancelled = true;
    };
  }, [loaded, requestedTab, ws, appendOpen]);

  const create = useCallback(
    async (body: WorksheetCreate = {}) => {
      try {
        const sheet = await worksheetsApi.create(ws, {
          title: timestampTitle(),
          ...body,
        });
        appendOpen(sheet);
        select(sheet.id);
        refreshBrowser();
        return sheet;
      } catch (err) {
        toast.error(
          err instanceof Error ? err.message : "Couldn't create a worksheet.",
        );
        return undefined;
      }
    },
    [ws, appendOpen, select, refreshBrowser],
  );

  const open = useCallback(
    async (id: string) => {
      if (worksheetsRef.current.some((w) => w.id === id)) {
        select(id);
        return;
      }
      try {
        const sheet = await worksheetsApi.update(ws, id, { is_open: true });
        appendOpen(sheet);
        select(sheet.id);
      } catch (err) {
        toast.error(
          err instanceof Error ? err.message : "Couldn't open that worksheet.",
        );
      }
    },
    [ws, appendOpen, select],
  );

  const edit = useCallback(
    (id: string, fields: WorksheetContent) => {
      const sheet = worksheetsRef.current.find((w) => w.id === id);
      patchCache(id, fields);
      sync.edit(id, fields, sheet?.version);
    },
    [patchCache, sync],
  );

  const setMeta = useCallback(
    (id: string, meta: WorksheetMeta) => {
      patchCache(id, meta);
      // Metadata is a remembered preference: a failed write costs only that,
      // and the choice still applies for as long as the page is open.
      worksheetsApi.update(ws, id, meta).catch(() => undefined);
    },
    [ws, patchCache],
  );

  const close = useCallback(
    async (id: string) => {
      const list = worksheetsRef.current;
      const index = list.findIndex((w) => w.id === id);
      const sheet = list[index];
      if (!sheet) return;
      const remaining = list.filter((w) => w.id !== id);
      dropFromCache(id);
      if ((active?.id ?? null) === id && remaining.length) {
        select(remaining[Math.min(index, remaining.length - 1)].id);
      }
      onClosed?.(id);
      try {
        await sync.flush(id);
        sync.forget(id);
        // A blank, unlinked draft is not worth keeping in the browser.
        if (!sheet.sql.trim() && !sheet.saved_query_id) {
          await worksheetsApi.remove(ws, id);
        } else {
          await worksheetsApi.update(ws, id, { is_open: false });
        }
      } catch {
        // It stays open on the server and comes back next load; nothing lost.
      }
      if (remaining.length === 0) await create();
      refreshBrowser();
    },
    [
      active?.id,
      dropFromCache,
      select,
      onClosed,
      sync,
      ws,
      create,
      refreshBrowser,
    ],
  );

  const rename = useCallback(
    (id: string, title: string) => {
      const trimmed = title.trim();
      if (!trimmed) return;
      edit(id, { title: trimmed });
      void sync.flush(id).then(refreshBrowser);
    },
    [edit, sync, refreshBrowser],
  );

  const remove = useCallback(
    async (id: string) => {
      const wasOpen = worksheetsRef.current.some((w) => w.id === id);
      if (wasOpen) {
        await close(id);
      }
      sync.forget(id);
      try {
        await worksheetsApi.remove(ws, id);
      } catch {
        // Already gone (close deletes blank drafts itself).
      }
      refreshBrowser();
    },
    [close, sync, ws, refreshBrowser],
  );

  const clearConflict = (id: string) =>
    setConflicts((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });

  const loadTheirs = useCallback(
    (id: string) => {
      const current = conflicts[id];
      if (!current) return;
      sync.takeTheirs(id);
      patchCache(id, {
        sql: current.sql,
        title: current.title,
        version: current.version,
        updated_at: current.updated_at,
      });
      clearConflict(id);
    },
    [conflicts, sync, patchCache],
  );

  const keepMine = useCallback(
    (id: string) => {
      clearConflict(id);
      void sync.keepMine(id);
    },
    [sync],
  );

  return {
    worksheets,
    isLoading: query.isLoading,
    active,
    select,
    create,
    open,
    close,
    rename,
    remove,
    edit,
    setMeta,
    flush: (id: string) => sync.flush(id),
    status,
    conflicts,
    loadTheirs,
    keepMine,
  };
}
