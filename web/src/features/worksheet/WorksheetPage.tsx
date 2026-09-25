import { useCallback, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useParams, useSearch } from "@tanstack/react-router";
import { toast } from "sonner";
import { AlertCircle, Check, PanelLeft, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { StorageLabel } from "@/components/app/StorageIcon";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useAgents } from "@/queries/agents";
import { useCatalogs } from "@/queries/catalogs";
import {
  useCancelQuery,
  useQuery_,
  useSavedQueries,
  useUpdateSavedQuery,
} from "@/queries/queries";
import { useWorkspace } from "@/queries/workspaces";
import { SaveAsMetricDialog } from "@/features/semantic/SaveAsMetricDialog";
import { agentAvailability } from "@/types/agent";
import type { SavedQuery } from "@/types/saved-query";
import { cn } from "@/utils";
import { useSqlCompletion } from "./completion/useSqlCompletion";
import { ConflictBanner } from "./ConflictBanner";
import { useOpenInWorksheet } from "./openInWorksheet";
import { ResultsPane } from "./ResultsPane";
import { SaveQueryDialog } from "./SaveQueryDialog";
import {
  RailSwitch,
  useRailView,
  WorksheetRail,
} from "./sidebar/WorksheetRail";
import { SqlEditor, type SqlEditorHandle } from "./SqlEditor";
import { loadLastUsedAgent, resolveWorksheetAgent } from "./state/resolveAgent";
import { useWorksheetRuntime } from "./state/useWorksheetRuntime";
import { useWorksheets } from "./state/useWorksheets";
import { useAssistantBridge } from "./useAssistantBridge";
import { DEFAULT_TIMEOUT_S, useRunWorksheet } from "./useRunWorksheet";
import { WorksheetTabs } from "./WorksheetTabs";
import { WorksheetToolbar } from "./WorksheetToolbar";

const modelPath = (worksheetId: string) => `worksheet-${worksheetId}.sql`;

export function WorksheetPage() {
  const { ws } = useParams({ from: "/$ws/worksheets" });
  const search = useSearch({ from: "/$ws/worksheets" });
  const { data: workspace } = useWorkspace(ws);
  const { data: agents = [], isLoading: agentsLoading } = useAgents();
  const { data: catalogs = [] } = useCatalogs(ws);
  const { data: savedQueries = [] } = useSavedQueries(ws);
  const qc = useQueryClient();
  const editorRef = useRef<SqlEditorHandle>(null);
  const runtime = useWorksheetRuntime();
  const onClosed = useCallback(
    (id: string) => {
      runtime.drop(id);
      editorRef.current?.disposeModel(modelPath(id));
    },
    [runtime],
  );
  const sheets = useWorksheets(ws, search.tab, onClosed);
  const sheet = sheets.active;
  const rt = runtime.get(sheet?.id);
  const openInWorksheet = useOpenInWorksheet(ws);

  // The catalog USEd for unqualified names: the worksheet's own while it is
  // still attached, else the workspace default.
  const defaultCatalog =
    catalogs.find((c) => c.is_default)?.slug ?? catalogs[0]?.slug;
  const catalog =
    sheet?.catalog && catalogs.some((c) => c.slug === sheet.catalog)
      ? sheet.catalog
      : defaultCatalog;
  useSqlCompletion(ws, catalog);

  const backend = workspace?.storage_backend_kind;
  const needs = useMemo(
    () => ({
      catalogKinds: catalogs.map((c) => c.kind),
      backends: backend ? [backend] : [],
    }),
    [catalogs, backend],
  );
  const resolution = resolveWorksheetAgent({
    agents,
    worksheetAgentId: sheet?.agent_id ?? null,
    lastUsedAgentId: loadLastUsedAgent(ws),
    ...needs,
  });
  const agent = agents.find((a) => a.id === resolution.agentId);

  const refreshCatalog = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ["workspace", ws, "catalog"] });
  }, [qc, ws]);
  const { run, isDispatching } = useRunWorksheet({
    ws,
    runtime,
    setMeta: sheets.setMeta,
    refreshCatalog,
  });

  const queryId =
    rt.queryId === undefined ? (sheet?.last_query_id ?? null) : rt.queryId;
  const { data: queryData } = useQuery_(queryId);
  const cancelQuery = useCancelQuery();
  const isRunning =
    queryData?.status === "queued" || queryData?.status === "running";

  const linked: SavedQuery | undefined = sheet?.saved_query_id
    ? savedQueries.find((q) => q.id === sheet.saved_query_id)
    : undefined;
  const unsavedIds = useMemo(() => {
    const byId = new Map(savedQueries.map((q) => [q.id, q]));
    return new Set(
      sheets.worksheets
        .filter((w) => {
          const saved = w.saved_query_id ? byId.get(w.saved_query_id) : null;
          return saved && saved.sql.trimEnd() !== w.sql.trimEnd();
        })
        .map((w) => w.id),
    );
  }, [savedQueries, sheets.worksheets]);
  const diverged = sheet ? unsavedIds.has(sheet.id) : false;

  const bridge = useAssistantBridge({
    activeId: sheet?.id,
    sql: sheet?.sql ?? "",
    catalog,
    editorRef,
    applySql: (id, sql) => sheets.edit(id, { sql }),
  });

  const [pickerOpen, setPickerOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [metricSql, setMetricSql] = useState<string | null>(null);
  const updateSaved = useUpdateSavedQuery(ws);

  function runText(text: string) {
    if (!sheet || !resolution.agentId) return;
    void run(sheet, text, { agentId: resolution.agentId, catalog });
  }

  async function saveLinked() {
    if (!sheet || !linked) return;
    try {
      await sheets.flush(sheet.id);
      await updateSaved.mutateAsync({
        id: linked.id,
        data: { sql: sheet.sql },
      });
      toast.success(`Saved to “${linked.name}”`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't save query.");
    }
  }

  function onSave() {
    if (linked) void saveLinked();
    else setSaveOpen(true);
  }

  const [leftWidth, setLeftWidth] = useState(280);
  const [editorHeight, setEditorHeight] = useState(55); // percent
  const isMobile = useMediaQuery("(max-width: 767px)");
  const [railOpen, setRailOpen] = useState(false);
  const [railView, setRailView] = useRailView();

  const onHorizMouseDown = useCallback(
    (e: React.MouseEvent) => {
      const startX = e.clientX;
      const startW = leftWidth;
      const onMove = (ev: MouseEvent) =>
        setLeftWidth(
          Math.max(200, Math.min(480, startW + ev.clientX - startX)),
        );
      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [leftWidth],
  );

  const onVertMouseDown = useCallback((e: React.MouseEvent) => {
    const container = (e.currentTarget as HTMLElement).closest(
      ".editor-results-container",
    ) as HTMLElement | null;
    const onMove = (ev: MouseEvent) => {
      if (!container) return;
      const rect = container.getBoundingClientRect();
      setEditorHeight(
        Math.max(
          20,
          Math.min(80, ((ev.clientY - rect.top) / rect.height) * 100),
        ),
      );
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, []);

  const openIds = useMemo(
    () => new Set(sheets.worksheets.map((w) => w.id)),
    [sheets.worksheets],
  );
  const rail = (onPicked?: () => void) => (
    <WorksheetRail
      ws={ws}
      workspaceName={workspace?.name}
      view={railView}
      openIds={openIds}
      activeId={sheet?.id}
      onOpen={(id) => {
        void sheets.open(id);
        onPicked?.();
      }}
      onOpenSaved={(q) => {
        void openInWorksheet({
          sql: q.sql,
          title: q.name,
          savedQueryId: q.id,
          agentId: q.default_agent_id ?? null,
        });
        onPicked?.();
      }}
      onRename={sheets.rename}
      onDelete={sheets.remove}
      onTableClick={onPicked}
    />
  );

  if (sheets.isLoading || !sheet) {
    return (
      <div className="flex h-full flex-col gap-2 p-4" aria-busy="true">
        <Skeleton className="h-6 w-64 animate-shimmer rounded" />
        <Skeleton className="h-40 w-full animate-shimmer rounded" />
      </div>
    );
  }

  const showNoAgent = !agentsLoading && resolution.agentId === null;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* One row across the page, as in Databricks: the sidebar's switch sits
          in a cell exactly as wide as the sidebar, so its right border and the
          sidebar's form one straight divider, and one bottom border runs under
          both. */}
      <div className="flex h-9 shrink-0 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)]">
        {!isMobile && (
          <div
            data-testid="rail-switch-cell"
            className="flex shrink-0 items-center border-r border-[var(--border-subtle)] px-2"
            style={{ width: leftWidth }}
          >
            <RailSwitch view={railView} onChange={setRailView} />
          </div>
        )}
        <WorksheetTabs
          worksheets={sheets.worksheets}
          activeId={sheet.id}
          unsavedIds={unsavedIds}
          onSelect={sheets.select}
          onClose={(id) => void sheets.close(id)}
          onRename={sheets.rename}
          onNew={() => void sheets.create()}
        />
      </div>

      <div className="flex flex-1 overflow-hidden">
        {!isMobile && (
          <div
            data-testid="worksheet-rail"
            className="relative shrink-0 border-r border-[var(--border-subtle)] bg-[var(--bg-surface)]"
            style={{ width: leftWidth }}
          >
            {rail()}
            {/* Straddles the border and takes no width of its own, so no gap
                opens between the sidebar and the toolbar. */}
            <div
              data-testid="rail-resize-handle"
              onMouseDown={onHorizMouseDown}
              className="absolute inset-y-0 -right-[3px] z-10 w-1.5 cursor-col-resize bg-transparent transition-colors hover:bg-[var(--border-strong)]"
              aria-hidden
            />
          </div>
        )}

        <div className="editor-results-container flex min-w-0 flex-1 flex-col overflow-hidden">
          <WorksheetToolbar
            ws={ws}
            leading={
              isMobile && (
                <Sheet open={railOpen} onOpenChange={setRailOpen}>
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
                  <SheetContent
                    side="left"
                    className="flex w-80 flex-col gap-0 p-0"
                  >
                    <SheetHeader className="border-b border-[var(--border-subtle)] px-4 py-3">
                      <SheetTitle className="text-sm">
                        Worksheets and catalog
                      </SheetTitle>
                    </SheetHeader>
                    <div className="border-b border-[var(--border-subtle)] px-2 py-1.5">
                      <RailSwitch view={railView} onChange={setRailView} />
                    </div>
                    <div className="min-h-0 flex-1 overflow-hidden">
                      {rail(() => setRailOpen(false))}
                    </div>
                  </SheetContent>
                </Sheet>
              )
            }
            agentId={resolution.agentId}
            onAgentChange={(id) => sheets.setMeta(sheet.id, { agent_id: id })}
            pickerOpen={pickerOpen}
            onPickerOpenChange={setPickerOpen}
            workspaceBackend={workspace?.storage_backend_kind ?? undefined}
            catalogs={catalogs}
            catalog={catalog}
            onCatalogChange={(slug) =>
              sheets.setMeta(sheet.id, { catalog: slug })
            }
            timeoutMinutes={(sheet.timeout_s ?? DEFAULT_TIMEOUT_S) / 60}
            onTimeoutChange={(minutes) =>
              sheets.setMeta(sheet.id, { timeout_s: minutes * 60 })
            }
            saveStatus={sheets.status[sheet.id]}
            isRunning={isRunning}
            runDisabled={isDispatching || !resolution.agentId}
            willStart={resolution.agentId !== null && resolution.willStart}
            onRun={() =>
              runText(editorRef.current?.getRunPayload() ?? sheet.sql)
            }
            onCancel={() => queryId && void cancelQuery.mutateAsync(queryId)}
            linkedName={linked?.name ?? null}
            diverged={diverged}
            onSave={onSave}
            onSaveAs={() => setSaveOpen(true)}
            onRevert={() =>
              linked && sheets.edit(sheet.id, { sql: linked.sql })
            }
            onSaveAsMetric={() => {
              const selected = editorRef.current
                ?.getSelectionRange()
                ?.text.trim();
              if (!selected) {
                toast.info(
                  "Select the expression first, for example SUM(total_amount).",
                );
                return;
              }
              setMetricSql(selected);
            }}
          />

          {showNoAgent && (
            <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2 text-xs text-text-secondary shrink-0">
              <AlertCircle className="size-3.5 text-[var(--status-running)] shrink-0" />
              <span>
                {resolution.agentId === null &&
                resolution.reason === "no-agents"
                  ? "No compute agents yet — add one to run queries."
                  : "No running agent can serve this workspace — start one or pick another."}
              </span>
              <Link
                to="/$ws/compute"
                params={{ ws }}
                className="font-medium text-[var(--brand-slate-blue)] hover:underline"
              >
                {resolution.agentId === null &&
                resolution.reason === "no-agents"
                  ? "Add an agent"
                  : "Manage compute"}
              </Link>
            </div>
          )}

          {sheets.conflicts[sheet.id] && (
            <ConflictBanner
              onLoadTheirs={() => sheets.loadTheirs(sheet.id)}
              onKeepMine={() => sheets.keepMine(sheet.id)}
            />
          )}

          {/* AI proposal bar: shown while an assistant-proposed edit awaits review */}
          {bridge.proposal && (
            <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] bg-[color-mix(in_oklab,var(--brand-yellow)_10%,var(--bg-surface))] px-3 py-1.5 text-xs shrink-0">
              <Sparkles className="size-3.5 text-[var(--brand-yellow)] shrink-0" />
              <span className="truncate text-text-primary">
                Assistant proposed changes
                {bridge.proposal.explanation
                  ? ` — ${bridge.proposal.explanation}`
                  : ""}
              </span>
              {bridge.proposal.note && (
                <span className="truncate italic text-text-tertiary">
                  {bridge.proposal.note}
                </span>
              )}
              <div className="ml-auto flex items-center gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 gap-1 text-xs"
                  onClick={bridge.reject}
                >
                  <X className="size-3.5" />
                  Reject
                </Button>
                <Button
                  size="sm"
                  className="h-7 gap-1 bg-[var(--brand-yellow)] text-black hover:bg-yellow-300 text-xs"
                  onClick={bridge.accept}
                >
                  <Check className="size-3.5" />
                  Accept
                </Button>
              </div>
            </div>
          )}

          <div
            className="overflow-hidden shrink-0"
            style={{ height: `${editorHeight}%` }}
          >
            <SqlEditor
              ref={editorRef}
              path={modelPath(sheet.id)}
              value={sheet.sql}
              onChange={(sql) => sheets.edit(sheet.id, { sql })}
              onRun={runText}
              onSave={onSave}
            />
          </div>

          <div
            onMouseDown={onVertMouseDown}
            className="h-1 cursor-row-resize bg-transparent hover:bg-[var(--border-strong)] transition-colors shrink-0"
            aria-hidden
          />

          <ResultsPane
            ws={ws}
            sheet={sheet}
            runtime={rt}
            onResultsTab={(tab) =>
              runtime.update(sheet.id, { resultsTab: tab })
            }
            onForgetLastQuery={() =>
              sheets.setMeta(sheet.id, { last_query_id: null })
            }
            onFixWithAssistant={(error) =>
              bridge.openPanel(
                `Fix this query error:\n\nSQL:\n${sheet.sql}\n\nError:\n${error}`,
              )
            }
            onSwitchAgent={() => setPickerOpen(true)}
          />
        </div>
      </div>

      {/* Status bar */}
      <div className="flex h-7 items-center gap-4 border-t border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 text-2xs text-text-secondary shrink-0">
        {agent && (
          <span className="flex items-center gap-1.5">
            <span
              className={cn(
                "size-1.5 rounded-full",
                agentAvailability(agent, needs).kind === "running"
                  ? "bg-[var(--status-success)]"
                  : "bg-[var(--text-tertiary)]",
              )}
              role="img"
              aria-label={agent.status}
              title={agent.status}
            />
            {agent.name}
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

      {saveOpen && (
        <SaveQueryDialog
          ws={ws}
          open
          onOpenChange={setSaveOpen}
          sql={sheet.sql}
          initialName={linked ? `${linked.name} (copy)` : sheet.title}
          agents={agents.filter((a) => {
            const kind = agentAvailability(a, needs).kind;
            return kind === "running" || kind === "startable";
          })}
          initialAgentId={resolution.agentId}
          onSaved={(saved) => {
            sheets.setMeta(sheet.id, { saved_query_id: saved.id });
            sheets.rename(sheet.id, saved.name);
            setSaveOpen(false);
            toast.success(`Saved “${saved.name}”`);
          }}
        />
      )}

      {metricSql !== null && (
        // Keyed on the selection so reopening with different SQL reseeds the
        // form rather than showing the previous expression.
        <SaveAsMetricDialog
          key={metricSql}
          ws={ws}
          sql={metricSql}
          open
          onOpenChange={(next) => !next && setMetricSql(null)}
        />
      )}
    </div>
  );
}
