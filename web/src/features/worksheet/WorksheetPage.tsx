import { useState, useCallback, useRef, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useParams, Link } from "@tanstack/react-router";
import { toast } from "sonner";
import {
  Play,
  Square,
  Save,
  Settings2,
  AlertCircle,
  PanelLeft,
  Sparkles,
  Check,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { useWorkspace } from "@/queries/workspaces";
import { useAgents } from "@/queries/agents";
import {
  useDispatchQuery,
  useCancelQuery,
  useQuery_,
  useQueryRows,
  useSaveQuery,
} from "@/queries/queries";
import { AgentPicker } from "@/components/app/AgentPicker";
import { StatusPill } from "@/components/app/StatusPill";
import { StorageLabel } from "@/components/app/StorageIcon";
import { CatalogTree } from "@/features/catalog/CatalogTree";
import { useCatalogs } from "@/queries/catalogs";
import { takePendingQuery } from "@/features/catalog/worksheetSql";
import { ProfilePanel } from "@/features/worksheet/profile/ProfilePanel";
import { SqlEditor, type SqlEditorHandle } from "./SqlEditor";
import { applyScopedEdit } from "./scopedEdit";
import { useSqlCompletion } from "./completion/useSqlCompletion";
import { splitStatements } from "./statements";
import { isDdl } from "./ddl";
import { queriesApi } from "@/api/queries";
import { useAssistant } from "@/features/assistant/AssistantContext";
import { ResultsTable } from "./ResultsTable";
import { cn, formatBytes } from "@/utils";

interface Tab {
  id: string;
  title: string;
  sql: string;
  dirty: boolean;
  // Set when the tab was opened from a saved query, so a run can stamp its
  // last_run_at on the backend.
  savedQueryId?: string;
}

const DEFAULT_SQL = `SELECT
  date_trunc('day', event_time) d,
  count(*) n
FROM raw.events
WHERE event_time >= '2026-05-01'
GROUP BY 1
ORDER BY 1;`;

// The landing/showcase workspace keeps example worksheets; every other
// workspace (including freshly created ones) starts blank — worksheets are
// scoped per workspace rather than shared.
const DEMO_WORKSPACE_SLUG = "acme-analytics";

const tabsStorageKey = (ws: string) => `dh-worksheets-${ws}`;

// The active query id is persisted (per workspace, per browser tab) so a refresh
// mid-execution recovers the running/completed query instead of dead-ending at
// "No results yet" — the existing polling rehydrates results once the id is set.
const activeQueryStorageKey = (ws: string) => `dh-active-query-${ws}`;

function loadActiveQueryId(ws: string): string | null {
  try {
    return sessionStorage.getItem(activeQueryStorageKey(ws));
  } catch {
    return null;
  }
}

// The active tab is persisted (per workspace) so leaving the worksheet for
// another page and returning restores the same tab, not the first one.
const activeTabStorageKey = (ws: string) => `dh-active-tab-${ws}`;

function loadActiveTab(ws: string): string | null {
  try {
    return sessionStorage.getItem(activeTabStorageKey(ws));
  } catch {
    return null;
  }
}

function seededTabs(ws: string): Tab[] {
  if (ws === DEMO_WORKSPACE_SLUG) {
    return [
      { id: "tab-1", title: "events.sql", sql: DEFAULT_SQL, dirty: false },
      {
        id: "tab-2",
        title: "funnel-draft",
        sql: "SELECT step, users, pct FROM analytics.funnel ORDER BY users DESC",
        dirty: true,
      },
    ];
  }
  return [{ id: "tab-1", title: "untitled", sql: "", dirty: false }];
}

function loadTabs(ws: string): Tab[] {
  try {
    const raw = localStorage.getItem(tabsStorageKey(ws));
    if (raw) {
      const parsed = JSON.parse(raw) as Tab[];
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch {
    // ignore malformed/unavailable storage
  }
  return seededTabs(ws);
}

export function WorksheetPage() {
  const { ws } = useParams({ from: "/$ws/worksheets" });
  const { data: workspace } = useWorkspace(ws);
  const { data: agents = [] } = useAgents();
  const qc = useQueryClient();

  // Active catalog: the one USEd for unqualified names + fed to completion.
  // Defaults to the workspace's default catalog; the user can switch it.
  const { data: catalogs = [] } = useCatalogs(ws);
  const [activeCatalog, setActiveCatalog] = useState<string | undefined>(
    undefined,
  );
  const resolvedCatalog =
    activeCatalog ??
    catalogs.find((c) => c.is_default)?.slug ??
    catalogs[0]?.slug;

  // Feed the active catalog + DuckDB metadata to the editor's autocomplete.
  useSqlCompletion(ws, resolvedCatalog);

  // After a successful DDL run, refresh the catalog tree and the autocomplete
  // caches so the new/altered/dropped object is usable without a manual refresh.
  const refreshCatalog = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["workspace", ws, "catalog"] });
  }, [qc, ws]);
  // The last dispatched statement streams to "done" via the reactive query
  // hooks rather than being awaited in runPayload, so remember whether it was
  // DDL and refresh once it completes (handled in an effect below).
  const lastRunWasDdl = useRef(false);

  const [tabs, setTabs] = useState<Tab[]>(() => loadTabs(ws));
  const [activeTab, setActiveTab] = useState(() => {
    const stored = loadActiveTab(ws);
    if (stored && tabs.some((t) => t.id === stored)) return stored;
    return tabs[0]?.id ?? "tab-1";
  });
  // Declared before the workspace-sync block below so that block can rehydrate
  // it on a workspace switch. Initialized from sessionStorage so a refresh
  // mid-execution recovers the running/completed query.
  const [activeQueryId, setActiveQueryId] = useState<string | null>(() =>
    loadActiveQueryId(ws),
  );
  // Results-area tab: the data grid or the inline profile panel.
  const [resultsTab, setResultsTab] = useState<"results" | "profile">(
    "results",
  );
  // Declared before the workspace-sync block so it can pre-select the agent a
  // saved query was opened with.
  const [agentId, setAgentId] = useState<string>(() => agents[0]?.id ?? "");

  // On first render and whenever the workspace changes, (re)load that
  // workspace's tabs and seed any SQL a catalog action stashed (e.g. Alter
  // table). Adjusting state during render is the React-sanctioned pattern for
  // syncing to a changing prop — see the matching block below.
  const [loadedWs, setLoadedWs] = useState<string | null>(null);
  if (loadedWs !== ws) {
    const base = loadedWs === null ? tabs : loadTabs(ws);
    const pending = takePendingQuery(ws);
    const next = pending
      ? [
          ...base,
          {
            id: `tab-seed-${ws}-${base.length}`,
            title: pending.savedQueryId ? "saved query" : "from catalog",
            sql: pending.sql,
            dirty: true,
            savedQueryId: pending.savedQueryId,
          },
        ]
      : base;
    setLoadedWs(ws);
    // On an actual workspace switch (not the initial mount, where the useState
    // initializer already loaded it), rehydrate the active query for the new
    // workspace so results don't bleed across workspaces.
    if (loadedWs !== null) {
      setActiveQueryId(loadActiveQueryId(ws));
    }
    // A saved query carries its default agent — pre-select it.
    if (pending?.agentId) {
      setAgentId(pending.agentId);
    }
    if (loadedWs !== null || pending) {
      setTabs(next);
      if (pending) {
        setActiveTab(next[next.length - 1].id);
      } else {
        const stored = loadActiveTab(ws);
        setActiveTab(
          stored && next.some((t) => t.id === stored) ? stored : next[0].id,
        );
      }
    }
  }

  // Persist tabs per workspace so they survive reloads and stay isolated.
  useEffect(() => {
    try {
      localStorage.setItem(tabsStorageKey(ws), JSON.stringify(tabs));
    } catch {
      // ignore unavailable storage
    }
  }, [ws, tabs]);

  // Persist the active tab so returning to the worksheet restores it.
  useEffect(() => {
    try {
      sessionStorage.setItem(activeTabStorageKey(ws), activeTab);
    } catch {
      // ignore unavailable storage
    }
  }, [ws, activeTab]);

  // Persist the active query id so a refresh mid-execution recovers it.
  useEffect(() => {
    try {
      if (activeQueryId) {
        sessionStorage.setItem(activeQueryStorageKey(ws), activeQueryId);
      } else {
        sessionStorage.removeItem(activeQueryStorageKey(ws));
      }
    } catch {
      // ignore unavailable storage
    }
  }, [ws, activeQueryId]);

  const [timeout, setTimeout_] = useState(10);
  const [dispatchError, setDispatchError] = useState<string | null>(null);
  // Progress across a multi-statement run (selection spanning several `;`).
  const [runSeq, setRunSeq] = useState<{ index: number; total: number } | null>(
    null,
  );
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  // Inline tab rename (double-click a tab): the tab being edited and its draft.
  const [editingTabId, setEditingTabId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [leftWidth, setLeftWidth] = useState(280);
  const [editorHeight, setEditorHeight] = useState(55); // percent
  const isMobile = useMediaQuery("(max-width: 767px)");
  const [catalogOpen, setCatalogOpen] = useState(false);

  const isDraggingVert = useRef(false);
  const isDraggingHoriz = useRef(false);
  // Synchronous re-entrancy guard for dispatch (see runPayload).
  const runLock = useRef(false);
  // Imperative handle to read the editor's current selection / cursor statement.
  const editorRef = useRef<SqlEditorHandle>(null);

  // Assistant ↔ editor bridge: lets the AI panel read the current SQL and propose
  // edits that the user accepts or rejects, with the changed lines highlighted.
  const { editorRef: assistantEditorRef } = useAssistant();
  const [proposal, setProposal] = useState<{
    tabId: string;
    oldSql: string;
    newSql: string;
    explanation: string;
    note?: string;
  } | null>(null);
  const currentSqlRef = useRef("");
  const resolvedCatalogRef = useRef<string | undefined>(undefined);
  // The selection last read by the assistant (at send() time), so a scoped
  // propose_edit response can be spliced back into the same range.
  const lastSelectionRef = useRef<{
    text: string;
    start: number;
    end: number;
  } | null>(null);
  const proposeEditRef = useRef<
    (sql: string, explanation: string, scoped: boolean) => void
  >(() => {});

  const currentTab = tabs.find((t) => t.id === activeTab);
  currentSqlRef.current = currentTab?.sql ?? "";
  resolvedCatalogRef.current = resolvedCatalog;
  const dispatchQuery = useDispatchQuery(ws);
  const cancelQuery = useCancelQuery();
  const saveQuery = useSaveQuery(ws);
  const { data: queryData } = useQuery_(activeQueryId);
  const queryRows = useQueryRows(activeQueryId, queryData?.status === "done");

  // The final statement of a run streams to "done" here (not awaited in
  // runPayload); refresh the catalog when that statement was DDL.
  useEffect(() => {
    if (queryData?.status === "done" && lastRunWasDdl.current) {
      lastRunWasDdl.current = false;
      refreshCatalog();
    }
  }, [queryData?.status, refreshCatalog]);

  const firstHealthyAgent = agents.find((a) => a.status === "healthy");
  const resolvedAgentId = agentId || firstHealthyAgent?.id || "";
  const resolvedAgent = agents.find((a) => a.id === resolvedAgentId);
  // Run requires an agent. When none is available (e.g. an object_store
  // workspace with no connected agents) explain how to enable it instead of
  // dead-ending.
  const needsAgent = !resolvedAgentId;

  function updateTabSql(sql: string) {
    setTabs((prev) =>
      prev.map((t) =>
        t.id === activeTab ? { ...t, sql, dirty: t.sql !== sql } : t,
      ),
    );
  }

  // Apply an AI-proposed edit to the active tab: swap in the new SQL (marking the
  // tab dirty) and remember the original so the user can reject. A scoped edit is
  // spliced into the selection it was requested for (see applyScopedEdit), falling
  // back to a full replace if the document changed since the request.
  const proposeEdit = (
    newSql: string,
    explanation: string,
    scoped: boolean,
  ) => {
    const oldSql = currentSqlRef.current;
    const applied = applyScopedEdit(
      oldSql,
      lastSelectionRef.current,
      newSql,
      scoped,
    );
    setProposal({
      tabId: activeTab,
      oldSql,
      newSql: applied.sql,
      explanation,
      note: applied.note,
    });
    setTabs((prev) =>
      prev.map((t) =>
        t.id === activeTab ? { ...t, sql: applied.sql, dirty: true } : t,
      ),
    );
  };
  // Keep the bridge pointing at the latest closure without re-registering it.
  proposeEditRef.current = proposeEdit;

  // Register the editor bridge for the assistant panel (once).
  useEffect(() => {
    assistantEditorRef.current = {
      getSql: () => currentSqlRef.current,
      proposeEdit: (sql, explanation, scoped) =>
        proposeEditRef.current(sql, explanation, scoped),
      getCatalog: () => resolvedCatalogRef.current ?? null,
      captureSelection: () => {
        const sel = editorRef.current?.getSelectionRange() ?? null;
        lastSelectionRef.current = sel;
        return sel;
      },
    };
    return () => {
      assistantEditorRef.current = null;
    };
  }, [assistantEditorRef, proposeEditRef, currentSqlRef]);

  // Highlight the proposed lines once the editor reflects the new SQL.
  useEffect(() => {
    if (proposal && proposal.tabId === activeTab) {
      editorRef.current?.highlightDiff(proposal.oldSql, proposal.newSql);
    }
  }, [proposal, activeTab]);

  function acceptProposal() {
    editorRef.current?.clearHighlight();
    setProposal(null);
  }

  function rejectProposal() {
    if (proposal) {
      const { tabId, oldSql } = proposal;
      setTabs((prev) =>
        prev.map((t) => (t.id === tabId ? { ...t, sql: oldSql } : t)),
      );
    }
    editorRef.current?.clearHighlight();
    setProposal(null);
  }

  function addTab() {
    const id = `tab-${Date.now()}`;
    setTabs((prev) => [
      ...prev,
      { id, title: "untitled", sql: "", dirty: false },
    ]);
    setActiveTab(id);
  }

  function closeTab(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    setTabs((prev) => {
      const next = prev.filter((t) => t.id !== id);
      if (activeTab === id && next.length > 0) {
        setActiveTab(next[next.length - 1].id);
      }
      return next.length === 0
        ? [{ id: "tab-new", title: "untitled", sql: "", dirty: false }]
        : next;
    });
  }

  function startRename(tab: Tab) {
    setEditingTabId(tab.id);
    setEditingTitle(tab.title);
  }

  function commitRename() {
    const id = editingTabId;
    const title = editingTitle.trim();
    setEditingTabId(null);
    if (id && title) {
      setTabs((prev) => prev.map((t) => (t.id === id ? { ...t, title } : t)));
    }
  }

  function insertTableSnippet(catalog: string, schema: string, table: string) {
    // Fully qualify so the snippet resolves regardless of the active catalog.
    const snippet = `SELECT * FROM ${catalog}.${schema}.${table} LIMIT 100`;
    setTabs((prev) =>
      prev.map((t) =>
        t.id === activeTab ? { ...t, sql: snippet, dirty: true } : t,
      ),
    );
  }

  function insertMetaViewSnippet(catalog: string, view: string) {
    // information_schema is DuckDB's global view set (not per-catalog), so scope
    // it by catalog. schemata keys on catalog_name; tables/views on table_catalog.
    const col = view === "schemata" ? "catalog_name" : "table_catalog";
    const snippet = `SELECT * FROM information_schema.${view} WHERE ${col} = '${catalog}' LIMIT 100`;
    setTabs((prev) =>
      prev.map((t) =>
        t.id === activeTab ? { ...t, sql: snippet, dirty: true } : t,
      ),
    );
  }

  // Run button: run the editor's selection, else the statement under the cursor.
  function runQuery() {
    void runPayload(
      editorRef.current?.getRunPayload() ?? currentTab?.sql ?? "",
    );
  }

  // Poll a query to a terminal status, mirroring useQuery_'s cadence. Used to
  // serialize a multi-statement run so each statement finishes before the next.
  async function waitForTerminal(id: string): Promise<string> {
    for (;;) {
      const q = await queriesApi.get(id);
      if (q.status !== "queued" && q.status !== "running") return q.status;
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
  }

  // Dispatch the statements of `text` sequentially. A single statement (the
  // cursor case) is one dispatch; a multi-statement selection runs each in turn,
  // waiting for the previous to finish and stopping if one does not succeed.
  async function runPayload(text: string) {
    const statements = splitStatements(text);
    if (statements.length === 0 || !resolvedAgentId) return;
    // A rapid double-click fires two click events in the same tick, before
    // React re-renders with dispatchQuery.isPending, so the reactive disabled
    // prop alone cannot stop the second dispatch. This ref lock is set
    // synchronously and held across the whole sequence.
    if (runLock.current) return;
    runLock.current = true;
    setActiveQueryId(null);
    setDispatchError(null);
    const multi = statements.length > 1;
    setRunSeq(null);
    lastRunWasDdl.current = false;
    try {
      for (let i = 0; i < statements.length; i++) {
        if (multi) setRunSeq({ index: i + 1, total: statements.length });
        const result = await dispatchQuery.mutateAsync({
          sql: statements[i],
          agentId: resolvedAgentId,
          opts: {
            timeout: timeout * 60,
            savedQueryId: currentTab?.savedQueryId,
            catalog: resolvedCatalog,
          },
        });
        setActiveQueryId(result.id);
        lastRunWasDdl.current = isDdl(statements[i]);
        // Wait for every statement but the last; abort the sequence if one
        // does not complete cleanly. The last streams via the reactive hooks.
        if (i < statements.length - 1) {
          const status = await waitForTerminal(result.id);
          if (status !== "done") break;
          // Refresh the catalog after each successful DDL statement (the last
          // statement's refresh is handled by the effect watching queryData).
          if (isDdl(statements[i])) refreshCatalog();
        }
      }
    } catch (err) {
      // A rejected dispatch (e.g. disallowed SQL → 422) never creates a query,
      // so surface its message here rather than relying on queryData.error.
      setDispatchError(
        err instanceof Error ? err.message : "Query failed to run.",
      );
    } finally {
      runLock.current = false;
    }
  }

  async function handleCancel() {
    if (!activeQueryId) return;
    await cancelQuery.mutateAsync(activeQueryId);
  }

  // Open the Save dialog, pre-filling the name from the tab title unless it's a
  // placeholder, so re-saving a named query overwrites it by name.
  function openSaveDialog() {
    const title = currentTab?.title ?? "";
    const placeholder = ["untitled", "from catalog", "saved query"].includes(
      title,
    );
    setSaveName(placeholder ? "" : title);
    setSaveOpen(true);
  }

  async function handleSave() {
    const name = saveName.trim();
    if (!name || !currentTab) return;
    try {
      await saveQuery.mutateAsync({
        name,
        sql: currentTab.sql,
        default_agent_id: resolvedAgentId || undefined,
      });
    } catch (err) {
      // Leave the dialog open so the user can retry after a failed save.
      toast.error(err instanceof Error ? err.message : "Couldn't save query.");
      return;
    }
    // Name the tab after the saved query and clear its unsaved marker.
    const id = currentTab.id;
    setTabs((prev) =>
      prev.map((t) => (t.id === id ? { ...t, title: name, dirty: false } : t)),
    );
    setSaveOpen(false);
    setSaveName("");
  }

  const isRunning =
    queryData?.status === "queued" || queryData?.status === "running";

  // Horizontal splitter (catalog width)
  const onHorizMouseDown = useCallback(
    (e: React.MouseEvent) => {
      isDraggingHoriz.current = true;
      const startX = e.clientX;
      const startW = leftWidth;
      const onMove = (ev: MouseEvent) => {
        if (!isDraggingHoriz.current) return;
        setLeftWidth(
          Math.max(160, Math.min(480, startW + ev.clientX - startX)),
        );
      };
      const onUp = () => {
        isDraggingHoriz.current = false;
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [leftWidth],
  );

  // Vertical splitter (editor/results height)
  const onVertMouseDown = useCallback((e: React.MouseEvent) => {
    isDraggingVert.current = true;
    const container = (e.currentTarget as HTMLElement).closest(
      ".editor-results-container",
    ) as HTMLElement | null;
    const onMove = (ev: MouseEvent) => {
      if (!isDraggingVert.current || !container) return;
      const rect = container.getBoundingClientRect();
      const pct = Math.max(
        20,
        Math.min(80, ((ev.clientY - rect.top) / rect.height) * 100),
      );
      setEditorHeight(pct);
    };
    const onUp = () => {
      isDraggingVert.current = false;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, []);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Tab strip */}
      <div className="flex h-9 items-center gap-1 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 overflow-x-auto shrink-0">
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="h-7 bg-transparent gap-0.5 p-0">
            {tabs.map((tab) => (
              <TabsTrigger
                key={tab.id}
                value={tab.id}
                onDoubleClick={() => startRename(tab)}
                className={cn(
                  "group h-7 gap-1.5 rounded-t-sm rounded-b-none border-b-2 px-3 text-xs data-[state=active]:border-[var(--brand-yellow)] data-[state=active]:bg-[var(--bg-canvas)] data-[state=inactive]:border-transparent",
                )}
              >
                {tab.dirty && editingTabId !== tab.id && (
                  <span
                    className="size-1.5 rounded-full bg-[var(--brand-orange)]"
                    aria-label="unsaved"
                  />
                )}
                {editingTabId === tab.id ? (
                  <input
                    value={editingTitle}
                    onChange={(e) => setEditingTitle(e.target.value)}
                    onBlur={commitRename}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commitRename();
                      else if (e.key === "Escape") setEditingTabId(null);
                      e.stopPropagation();
                    }}
                    // Don't let clicks bubble to the tab trigger while editing.
                    onClick={(e) => e.stopPropagation()}
                    autoFocus
                    aria-label="Rename worksheet"
                    className="w-24 bg-transparent text-xs outline-none border-b border-[var(--brand-yellow)]"
                  />
                ) : (
                  tab.title
                )}
                <button
                  type="button"
                  onClick={(e) => closeTab(tab.id, e)}
                  className="ml-0.5 hidden rounded opacity-60 hover:opacity-100 group-hover:inline-flex"
                  aria-label={`Close ${tab.title}`}
                >
                  ×
                </button>
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <button
          type="button"
          onClick={addTab}
          className="ml-1 flex size-6 items-center justify-center rounded text-text-secondary hover:bg-accent hover:text-text-primary text-sm"
          aria-label="New worksheet"
        >
          +
        </button>
      </div>

      {/* Three-pane layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* Catalog pane — inline on desktop, a drawer on mobile */}
        {!isMobile && (
          <>
            <div
              className="shrink-0 overflow-hidden border-r border-[var(--border-subtle)] bg-[var(--bg-surface)]"
              style={{ width: leftWidth }}
            >
              <div className="h-full">
                <CatalogTree
                  ws={ws}
                  workspaceName={workspace?.name ?? ws}
                  onTableClick={insertTableSnippet}
                  onMetaViewClick={insertMetaViewSnippet}
                />
              </div>
            </div>

            {/* Horizontal drag handle */}
            <div
              onMouseDown={onHorizMouseDown}
              className="w-1 cursor-col-resize bg-transparent hover:bg-[var(--border-strong)] transition-colors shrink-0"
              aria-hidden
            />
          </>
        )}

        {/* Editor + results */}
        <div className="editor-results-container flex min-w-0 flex-1 flex-col overflow-hidden">
          {/* Editor toolbar */}
          <div className="flex flex-wrap items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-1.5 shrink-0">
            {isMobile && (
              <Sheet open={catalogOpen} onOpenChange={setCatalogOpen}>
                <SheetTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8 shrink-0"
                    aria-label="Show tables"
                  >
                    <PanelLeft className="size-4" />
                  </Button>
                </SheetTrigger>
                <SheetContent side="left" className="w-80 p-0">
                  <SheetHeader className="border-b border-[var(--border-subtle)] px-4 py-3">
                    <SheetTitle className="text-sm">Tables</SheetTitle>
                  </SheetHeader>
                  <div className="h-[calc(100%-3.25rem)] overflow-auto">
                    <CatalogTree
                      ws={ws}
                      workspaceName={workspace?.name ?? ws}
                      onTableClick={(catalog, schema, table) => {
                        insertTableSnippet(catalog, schema, table);
                        setCatalogOpen(false);
                      }}
                    />
                  </div>
                </SheetContent>
              </Sheet>
            )}
            <AgentPicker
              value={resolvedAgentId}
              onChange={setAgentId}
              workspaceBackend={workspace?.storage_backend_kind ?? undefined}
            />

            {catalogs.length > 0 && (
              <select
                aria-label="Active catalog"
                value={resolvedCatalog ?? ""}
                onChange={(e) => setActiveCatalog(e.target.value)}
                className="h-8 rounded border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 text-xs text-text-primary"
                title="Active catalog (USEd for unqualified table names)"
              >
                {catalogs.map((c) => (
                  <option key={c.id} value={c.slug}>
                    {c.slug}
                    {c.is_default ? " (default)" : ""}
                  </option>
                ))}
              </select>
            )}

            <Popover>
              <PopoverTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  aria-label="Query settings"
                >
                  <Settings2 className="size-4" />
                </Button>
              </PopoverTrigger>
              <PopoverContent className="w-56 space-y-3 p-3" align="start">
                <div className="space-y-1">
                  <Label className="text-xs">Timeout (min)</Label>
                  <Input
                    type="number"
                    min={1}
                    max={120}
                    value={timeout}
                    onChange={(e) => setTimeout_(Number(e.target.value))}
                    className="h-7 text-xs"
                  />
                </div>
              </PopoverContent>
            </Popover>

            <div className="ml-auto flex items-center gap-1">
              {isRunning ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 gap-1.5 text-xs"
                  onClick={handleCancel}
                >
                  <Square className="size-3" />
                  Cancel
                </Button>
              ) : (
                <Button
                  size="sm"
                  className="h-8 gap-1.5 bg-[var(--brand-yellow)] text-black hover:bg-yellow-300 text-xs font-medium animate-run-pulse-trigger"
                  onClick={runQuery}
                  disabled={dispatchQuery.isPending || !resolvedAgentId}
                  aria-label="Run query (⌘↵)"
                >
                  <Play className="size-3 fill-black" />
                  Run
                  <kbd className="ml-0.5 font-mono text-2xs opacity-60">⌘↵</kbd>
                </Button>
              )}
              <Button
                variant="ghost"
                size="sm"
                className="h-8 text-xs"
                onClick={openSaveDialog}
              >
                <Save className="size-3.5 mr-1" />
                Save…
              </Button>
            </div>
          </div>

          {needsAgent && (
            <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2 text-xs text-text-secondary shrink-0">
              <AlertCircle className="size-3.5 text-[var(--status-running)] shrink-0" />
              <span>No agents connected — connect one to run queries.</span>
              <Link
                to="/$ws/compute"
                params={{ ws }}
                className="font-medium text-[var(--brand-slate-blue)] hover:underline"
              >
                Add an agent
              </Link>
            </div>
          )}

          {/* AI proposal bar: shown while an assistant-proposed edit awaits review */}
          {proposal && proposal.tabId === activeTab && (
            <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] bg-[color-mix(in_oklab,var(--brand-yellow)_10%,var(--bg-surface))] px-3 py-1.5 text-xs shrink-0">
              <Sparkles className="size-3.5 text-[var(--brand-yellow)] shrink-0" />
              <span className="truncate text-text-primary">
                Assistant proposed changes
                {proposal.explanation ? ` — ${proposal.explanation}` : ""}
              </span>
              {proposal.note && (
                <span className="truncate italic text-text-tertiary">
                  {proposal.note}
                </span>
              )}
              <div className="ml-auto flex items-center gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 gap-1 text-xs"
                  onClick={rejectProposal}
                >
                  <X className="size-3.5" />
                  Reject
                </Button>
                <Button
                  size="sm"
                  className="h-7 gap-1 bg-[var(--brand-yellow)] text-black hover:bg-yellow-300 text-xs"
                  onClick={acceptProposal}
                >
                  <Check className="size-3.5" />
                  Accept
                </Button>
              </div>
            </div>
          )}

          {/* Editor */}
          <div
            className="overflow-hidden shrink-0"
            style={{ height: `${editorHeight}%` }}
          >
            <SqlEditor
              ref={editorRef}
              value={currentTab?.sql ?? ""}
              onChange={updateTabSql}
              onRun={runPayload}
              onSave={openSaveDialog}
            />
          </div>

          {/* Vertical drag handle */}
          <div
            onMouseDown={onVertMouseDown}
            className="h-1 cursor-row-resize bg-transparent hover:bg-[var(--border-strong)] transition-colors shrink-0"
            aria-hidden
          />

          {/* Results pane */}
          <div className="flex flex-1 flex-col overflow-hidden border-t border-[var(--border-subtle)]">
            {/* Results header */}
            <div className="flex items-center gap-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-1.5 shrink-0">
              <div className="flex items-center gap-0.5" role="tablist">
                {(["results", "profile"] as const).map((tab) => (
                  <button
                    key={tab}
                    type="button"
                    role="tab"
                    aria-selected={resultsTab === tab}
                    onClick={() => setResultsTab(tab)}
                    className={cn(
                      "rounded px-2 py-0.5 text-xs font-medium capitalize",
                      resultsTab === tab
                        ? "bg-[var(--bg-elevated)] text-text-primary"
                        : "text-text-secondary hover:text-text-primary",
                    )}
                  >
                    {tab}
                  </button>
                ))}
              </div>
              {dispatchError && (
                <span className="text-xs font-medium text-[var(--status-failed)]">
                  Failed
                </span>
              )}
              {runSeq && runSeq.total > 1 && (
                <span className="text-xs text-text-secondary font-tabular">
                  Statement {runSeq.index}/{runSeq.total}
                </span>
              )}
              {queryData && (
                <>
                  <StatusPill
                    status={queryData.status}
                    startedAt={queryData.started_at}
                    durationMs={queryData.duration_ms}
                  />
                  {queryData.status === "done" &&
                    queryData.row_count != null && (
                      <span className="text-xs text-text-secondary font-tabular">
                        {queryData.row_count.toLocaleString()} rows
                      </span>
                    )}
                  {queryData.status === "done" &&
                    queryData.result_bytes != null && (
                      <span className="text-xs text-text-secondary font-tabular">
                        {formatBytes(queryData.result_bytes)}
                      </span>
                    )}
                  {queryData.status === "running" &&
                    typeof queryData.progress?.stage === "string" && (
                      <span className="text-xs text-text-secondary">
                        {queryData.progress.stage}
                      </span>
                    )}
                </>
              )}
              {resultsTab === "profile" &&
                activeQueryId &&
                queryData?.status === "done" && (
                  <Link
                    to="/$ws/queries/$queryId"
                    params={{ ws, queryId: activeQueryId }}
                    className="ml-auto text-2xs text-text-secondary hover:text-text-primary"
                  >
                    Open full profile ↗
                  </Link>
                )}
            </div>

            <div className="flex-1 overflow-hidden">
              {resultsTab === "profile" ? (
                <ProfilePanel
                  queryId={activeQueryId}
                  enabled={queryData?.status === "done"}
                />
              ) : (
                <ResultsTable
                  columns={queryRows.columns}
                  rows={queryRows.rows}
                  total={queryRows.total}
                  error={
                    dispatchError ??
                    (queryData?.status === "failed" ? queryData.error : null)
                  }
                  isLoading={
                    queryRows.isLoading && queryData?.status === "running"
                  }
                  onLoadMore={queryRows.fetchNextPage}
                  hasMore={queryRows.hasNextPage}
                  isLoadingMore={queryRows.isFetchingNextPage}
                />
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Status bar */}
      <div className="flex h-7 items-center gap-4 border-t border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 text-2xs text-text-secondary shrink-0">
        {resolvedAgent && (
          <span className="flex items-center gap-1.5">
            <span
              className={cn(
                "size-1.5 rounded-full",
                resolvedAgent.status === "healthy"
                  ? "bg-[var(--status-success)]"
                  : "bg-[var(--status-failed)]",
              )}
              role="img"
              aria-label={resolvedAgent.status}
              title={resolvedAgent.status}
            />
            {resolvedAgent.name}
          </span>
        )}
        <span className="text-[var(--border-strong)]">·</span>
        <span>
          {workspace?.name ?? ws}
          {workspace && (
            <>
              {" "}
              (<StorageLabel kind={workspace.storage_backend_kind} />)
            </>
          )}
        </span>
        {queryData?.duration_ms != null && queryData.status === "done" && (
          <>
            <span className="text-[var(--border-strong)]">·</span>
            <span className="font-tabular">
              {(queryData.duration_ms / 1000).toFixed(1)}s
            </span>
          </>
        )}
      </div>

      {/* Save dialog */}
      <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Save query</DialogTitle>
            <DialogDescription>
              Save the current worksheet SQL as a named, reusable query.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label htmlFor="save-name" className="text-sm">
              Name
            </Label>
            <Input
              id="save-name"
              placeholder="My query name"
              value={saveName}
              onChange={(e) => setSaveName(e.target.value)}
              autoFocus
              onKeyDown={(e) => e.key === "Enter" && handleSave()}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSaveOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleSave}
              disabled={!saveName.trim() || saveQuery.isPending}
            >
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
