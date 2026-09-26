import { useCallback, useRef } from "react";
import { ApiError } from "@/api/client";
import { queriesApi } from "@/api/queries";
import { useDispatchQuery } from "@/queries/queries";
import type { Worksheet, WorksheetMeta } from "@/types/worksheet";
import { splitStatements } from "./statements";
import { isDdl } from "./ddl";
import type { WorksheetRuntime } from "./state/useWorksheetRuntime";
import { storeLastUsedAgent } from "./state/resolveAgent";

// The worksheet timeout when none is set, in seconds (the API's own default).
export const DEFAULT_TIMEOUT_S = 600;

// Poll a query to a terminal status, at useQuery_'s cadence.
async function waitForTerminal(id: string): Promise<string> {
  for (;;) {
    const q = await queriesApi.get(id);
    if (q.status !== "queued" && q.status !== "running") return q.status;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
}

/**
 * Runs SQL from a worksheet. Everything a run produces lands on the worksheet it
 * started from, so switching tabs mid-run neither loses the run nor shows its
 * results in the wrong tab.
 */
export function useRunWorksheet({
  ws,
  runtime,
  setMeta,
  refreshCatalog,
}: {
  ws: string;
  runtime: WorksheetRuntime;
  setMeta: (id: string, meta: WorksheetMeta) => void;
  refreshCatalog: () => void;
}) {
  const dispatchQuery = useDispatchQuery(ws);
  // Synchronous re-entrancy guard per worksheet: a rapid double-click fires two
  // clicks before React re-renders the button disabled.
  const locks = useRef(new Set<string>());
  const { mutateAsync } = dispatchQuery;
  const { update } = runtime;

  const run = useCallback(
    async (
      sheet: Worksheet,
      text: string,
      target: { agentId: string; catalog?: string },
    ) => {
      const statements = splitStatements(text);
      if (statements.length === 0 || !target.agentId) return;
      if (locks.current.has(sheet.id)) return;
      locks.current.add(sheet.id);
      update(sheet.id, { queryId: null, dispatchError: null, runSeq: null });
      const multi = statements.length > 1;
      try {
        for (let i = 0; i < statements.length; i++) {
          if (multi)
            update(sheet.id, {
              runSeq: { index: i + 1, total: statements.length },
            });
          const result = await mutateAsync({
            sql: statements[i],
            agentId: target.agentId,
            opts: {
              timeout: sheet.timeout_s ?? DEFAULT_TIMEOUT_S,
              savedQueryId: sheet.saved_query_id ?? undefined,
              catalog: target.catalog,
            },
          });
          update(sheet.id, { queryId: result.id });
          setMeta(sheet.id, {
            last_query_id: result.id,
            agent_id: target.agentId,
          });
          storeLastUsedAgent(ws, target.agentId);
          const last = i === statements.length - 1;
          if (!last) {
            // Each statement finishes before the next; a failure stops the rest.
            const status = await waitForTerminal(result.id);
            if (status !== "done") break;
            if (isDdl(statements[i])) refreshCatalog();
          } else if (isDdl(statements[i])) {
            void waitForTerminal(result.id).then((status) => {
              if (status === "done") refreshCatalog();
            });
          }
        }
      } catch (err) {
        // A rejected dispatch has no running query to report its error. An
        // unreachable agent is the exception: the API records the attempt, so
        // show that run, which History lists too.
        const failedId =
          err instanceof ApiError && typeof err.details?.query_id === "string"
            ? err.details.query_id
            : null;
        update(sheet.id, {
          queryId: failedId,
          dispatchError: {
            message:
              err instanceof Error ? err.message : "Query failed to run.",
            code: err instanceof ApiError ? err.code : undefined,
          },
        });
        if (failedId) setMeta(sheet.id, { last_query_id: failedId });
      } finally {
        locks.current.delete(sheet.id);
      }
    },
    [mutateAsync, update, setMeta, ws, refreshCatalog],
  );

  return { run, isDispatching: dispatchQuery.isPending };
}
