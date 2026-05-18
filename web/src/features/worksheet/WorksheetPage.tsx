import { useState, useCallback, useRef } from "react";
import { useParams } from "@tanstack/react-router";
import { Play, Square, Save, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
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
import { CatalogTree } from "./CatalogTree";
import { SqlEditor } from "./SqlEditor";
import { ResultsTable } from "./ResultsTable";
import { cn } from "@/utils";

interface Tab {
  id: string;
  title: string;
  sql: string;
  dirty: boolean;
}

const DEFAULT_SQL = `SELECT
  date_trunc('day', event_time) d,
  count(*) n
FROM raw.events
WHERE event_time >= '2026-05-01'
GROUP BY 1
ORDER BY 1;`;

export function WorksheetPage() {
  const { ws } = useParams({ from: "/$ws/worksheets" });
  const { data: workspace } = useWorkspace(ws);
  const { data: agents = [] } = useAgents();

  const [tabs, setTabs] = useState<Tab[]>([
    { id: "tab-1", title: "events.sql", sql: DEFAULT_SQL, dirty: false },
    {
      id: "tab-2",
      title: "funnel-draft",
      sql: "SELECT step, users, pct FROM analytics.funnel ORDER BY users DESC",
      dirty: true,
    },
  ]);
  const [activeTab, setActiveTab] = useState("tab-1");
  const [agentId, setAgentId] = useState<string>(() => agents[0]?.id ?? "");
  const [memoryLimit, setMemoryLimit] = useState(6);
  const [timeout, setTimeout_] = useState(10);
  const [activeQueryId, setActiveQueryId] = useState<string | null>(null);
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [leftWidth, setLeftWidth] = useState(280);
  const [editorHeight, setEditorHeight] = useState(55); // percent

  const isDraggingVert = useRef(false);
  const isDraggingHoriz = useRef(false);

  const currentTab = tabs.find((t) => t.id === activeTab);
  const dispatchQuery = useDispatchQuery(ws);
  const cancelQuery = useCancelQuery();
  const saveQuery = useSaveQuery(ws);
  const { data: queryData } = useQuery_(activeQueryId);
  const { data: rowsData, isLoading: rowsLoading } = useQueryRows(
    activeQueryId,
    queryData?.status === "done",
  );

  const firstHealthyAgent = agents.find((a) => a.status === "healthy");
  const resolvedAgentId = agentId || firstHealthyAgent?.id || "";
  const resolvedAgent = agents.find((a) => a.id === resolvedAgentId);

  function updateTabSql(sql: string) {
    setTabs((prev) =>
      prev.map((t) =>
        t.id === activeTab ? { ...t, sql, dirty: t.sql !== sql } : t,
      ),
    );
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

  function insertTableSnippet(schema: string, table: string) {
    const snippet = `SELECT * FROM ${schema}.${table} LIMIT 100`;
    setTabs((prev) =>
      prev.map((t) =>
        t.id === activeTab ? { ...t, sql: snippet, dirty: true } : t,
      ),
    );
  }

  async function runQuery() {
    if (!currentTab?.sql.trim() || !resolvedAgentId) return;
    setActiveQueryId(null);
    const result = await dispatchQuery.mutateAsync({
      sql: currentTab.sql,
      agentId: resolvedAgentId,
      opts: { memory_limit: memoryLimit, timeout: timeout * 60 },
    });
    setActiveQueryId(result.id);
  }

  async function handleCancel() {
    if (!activeQueryId) return;
    await cancelQuery.mutateAsync(activeQueryId);
  }

  async function handleSave() {
    if (!saveName.trim() || !currentTab) return;
    await saveQuery.mutateAsync({
      name: saveName,
      sql: currentTab.sql,
      default_agent_id: resolvedAgentId || undefined,
    });
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
                className={cn(
                  "group h-7 gap-1.5 rounded-t-sm rounded-b-none border-b-2 px-3 text-xs data-[state=active]:border-[var(--brand-yellow)] data-[state=active]:bg-[var(--bg-canvas)] data-[state=inactive]:border-transparent",
                )}
              >
                {tab.dirty && (
                  <span
                    className="size-1.5 rounded-full bg-[var(--brand-orange)]"
                    aria-label="unsaved"
                  />
                )}
                {tab.title}
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
        {/* Catalog pane */}
        <div
          className="shrink-0 overflow-hidden border-r border-[var(--border-subtle)] bg-[var(--bg-surface)]"
          style={{ width: leftWidth }}
        >
          <div className="h-full">
            <CatalogTree
              ws={ws}
              workspaceName={workspace?.name ?? ws}
              onTableClick={insertTableSnippet}
            />
          </div>
        </div>

        {/* Horizontal drag handle */}
        <div
          onMouseDown={onHorizMouseDown}
          className="w-1 cursor-col-resize bg-transparent hover:bg-[var(--border-strong)] transition-colors shrink-0"
          aria-hidden
        />

        {/* Editor + results */}
        <div className="editor-results-container flex flex-1 flex-col overflow-hidden">
          {/* Editor toolbar */}
          <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-1.5 shrink-0">
            <AgentPicker
              value={resolvedAgentId}
              onChange={setAgentId}
              workspaceBackend={workspace?.storage_backend_kind}
            />

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
                  <Label className="text-xs">Memory limit (GB)</Label>
                  <Input
                    type="number"
                    min={1}
                    max={64}
                    value={memoryLimit}
                    onChange={(e) => setMemoryLimit(Number(e.target.value))}
                    className="h-7 text-xs"
                  />
                </div>
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
                onClick={() => setSaveOpen(true)}
              >
                <Save className="size-3.5 mr-1" />
                Save…
              </Button>
            </div>
          </div>

          {/* Editor */}
          <div
            className="overflow-hidden shrink-0"
            style={{ height: `${editorHeight}%` }}
          >
            <SqlEditor value={currentTab?.sql ?? ""} onChange={updateTabSql} />
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
              <span className="text-xs font-medium text-text-secondary">
                Results
              </span>
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
                  {queryData.status === "failed" && queryData.error && (
                    <span
                      className="text-xs text-[var(--status-failed)] truncate max-w-xs"
                      role="alert"
                    >
                      {queryData.error}
                    </span>
                  )}
                </>
              )}
            </div>

            <div className="flex-1 overflow-hidden">
              <ResultsTable
                columns={rowsData?.columns ?? []}
                rows={rowsData?.rows ?? []}
                total={rowsData?.total ?? 0}
                isLoading={rowsLoading && queryData?.status === "running"}
              />
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
