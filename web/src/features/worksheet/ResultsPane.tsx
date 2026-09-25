import { useEffect } from "react";
import { Link } from "@tanstack/react-router";
import { ArrowRightLeft } from "lucide-react";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/app/StatusPill";
import { useQuery_, useQueryRows } from "@/queries/queries";
import type { Worksheet } from "@/types/worksheet";
import { cn, formatBytes } from "@/utils";
import { ProfilePanel } from "./profile/ProfilePanel";
import { ResultsTable } from "./ResultsTable";
import type { TabRuntime } from "./state/useWorksheetRuntime";

const NOT_CONNECTED = "Agent not connected";

const squash = (sql: string) => sql.replace(/\s+/g, " ").trim().toLowerCase();

/** Whether the run's statement no longer appears in the worksheet as written. */
export function resultsAreStale(worksheetSql: string, ranSql: string): boolean {
  const ran = squash(ranSql).replace(/;$/, "");
  return ran.length > 0 && !squash(worksheetSql).includes(ran);
}

function rowsErrorMessage(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  if (err.status === 410)
    return "These results have expired: agents keep them for 24 hours. Run the query again.";
  if (err.status === 503)
    return "The agent that ran this query is offline, so its results can't be read until it reconnects.";
  return null;
}

interface ResultsPaneProps {
  ws: string;
  sheet: Worksheet;
  runtime: TabRuntime;
  onResultsTab: (tab: TabRuntime["resultsTab"]) => void;
  // The saved `last_query_id` names a query that no longer exists.
  onForgetLastQuery: () => void;
  onFixWithAssistant: (error: string) => void;
  onSwitchAgent: () => void;
}

/** The results and profile of the active worksheet's latest run. */
export function ResultsPane({
  ws,
  sheet,
  runtime,
  onResultsTab,
  onForgetLastQuery,
  onFixWithAssistant,
  onSwitchAgent,
}: ResultsPaneProps) {
  const queryId =
    runtime.queryId === undefined ? sheet.last_query_id : runtime.queryId;
  const { data: queryData, error: queryError } = useQuery_(queryId);
  const queryRows = useQueryRows(queryId, queryData?.status === "done");
  const { resultsTab, runSeq, dispatchError } = runtime;

  const missing = queryError instanceof ApiError && queryError.status === 404;
  useEffect(() => {
    if (missing && runtime.queryId === undefined) onForgetLastQuery();
  }, [missing, runtime.queryId, onForgetLastQuery]);

  const resultError =
    dispatchError?.message ??
    (queryData?.status === "failed" ? queryData.error : null) ??
    rowsErrorMessage(queryRows.error);
  const unreachable =
    dispatchError?.code === "unavailable" || resultError === NOT_CONNECTED;
  const stale =
    queryData?.status === "done" && resultsAreStale(sheet.sql, queryData.sql);

  return (
    <div className="flex flex-1 flex-col overflow-hidden border-t border-[var(--border-subtle)]">
      <div className="flex flex-wrap items-center gap-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-1.5 shrink-0">
        <div className="flex items-center gap-0.5" role="tablist">
          {(["results", "profile"] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={resultsTab === tab}
              onClick={() => onResultsTab(tab)}
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
        {queryData && !dispatchError && (
          <>
            <StatusPill
              status={queryData.status}
              startedAt={queryData.started_at}
              durationMs={queryData.duration_ms}
            />
            {queryData.status === "done" && queryData.row_count != null && (
              <span className="text-xs text-text-secondary font-tabular">
                {queryData.row_count.toLocaleString()} rows
              </span>
            )}
            {queryData.status === "done" && queryData.result_bytes != null && (
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
        {stale && (
          <span
            className="rounded bg-accent px-1.5 py-0.5 text-2xs text-text-secondary"
            title="The SQL that produced these results has since changed in the editor."
          >
            From an earlier version of this query
          </span>
        )}
        {resultsTab === "profile" &&
          queryId &&
          queryData?.status === "done" && (
            <Link
              to="/$ws/queries/$queryId"
              params={{ ws, queryId }}
              className="ml-auto text-2xs text-text-secondary hover:text-text-primary"
            >
              Open full profile ↗
            </Link>
          )}
      </div>

      <div className="flex-1 overflow-hidden">
        {resultsTab === "profile" ? (
          <ProfilePanel
            queryId={queryId ?? null}
            enabled={queryData?.status === "done"}
          />
        ) : (
          <ResultsTable
            columns={queryRows.columns}
            columnSchema={queryRows.columnSchema}
            rows={queryRows.rows}
            total={queryRows.total}
            error={resultError}
            errorActions={
              unreachable ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 gap-1.5 text-xs"
                  onClick={onSwitchAgent}
                >
                  <ArrowRightLeft className="size-3.5" />
                  Switch agent
                </Button>
              ) : undefined
            }
            onFixWithAssistant={
              resultError && !unreachable
                ? () => onFixWithAssistant(resultError)
                : undefined
            }
            isLoading={queryRows.isLoading && queryData?.status === "running"}
            onLoadMore={queryRows.fetchNextPage}
            hasMore={queryRows.hasNextPage}
            isLoadingMore={queryRows.isFetchingNextPage}
          />
        )}
      </div>
    </div>
  );
}
