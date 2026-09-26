import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { worksheetsApi } from "@/api/worksheets";
import { openWorksheetsKey, worksheetsKey } from "@/queries/worksheets";
import type { Worksheet, WorksheetCreate } from "@/types/worksheet";

export interface OpenInWorksheetRequest {
  sql: string;
  // The new tab's name: the saved query's, or the table the SQL is about.
  title: string;
  savedQueryId?: string;
  agentId?: string | null;
  catalog?: string | null;
}

async function createWorksheet(
  ws: string,
  body: WorksheetCreate,
): Promise<Worksheet> {
  try {
    return await worksheetsApi.create(ws, body);
  } catch (err) {
    // A saved query's default agent may be one this user cannot use; the tab
    // still opens and picks an agent itself.
    if (
      body.agent_id &&
      err instanceof ApiError &&
      [403, 404].includes(err.status)
    ) {
      return worksheetsApi.create(ws, { ...body, agent_id: null });
    }
    throw err;
  }
}

/**
 * Open SQL from elsewhere in the app as a worksheet tab.
 *
 * A saved query already open in a worksheet (or closed in one) focuses that
 * worksheet, with whatever edits it holds, rather than opening a duplicate.
 */
export function useOpenInWorksheet(ws: string) {
  const qc = useQueryClient();
  const navigate = useNavigate();

  return useCallback(
    async (req: OpenInWorksheetRequest) => {
      let sheet: Worksheet | undefined;
      try {
        if (req.savedQueryId) {
          const found = await worksheetsApi.list(ws, {
            saved_query_id: req.savedQueryId,
            sort: "updated_at",
            limit: 1,
          });
          sheet = found.items[0];
          if (sheet && !sheet.is_open) {
            sheet = await worksheetsApi.update(ws, sheet.id, { is_open: true });
          }
        }
        sheet ??= await createWorksheet(ws, {
          title: req.title,
          sql: req.sql,
          saved_query_id: req.savedQueryId ?? null,
          agent_id: req.agentId ?? null,
          catalog: req.catalog ?? null,
        });
      } catch (err) {
        toast.error(
          err instanceof Error ? err.message : "Couldn't open a worksheet.",
        );
        return;
      }
      const opened = sheet;
      qc.setQueryData<Worksheet[]>(openWorksheetsKey(ws), (list) =>
        !list || list.some((w) => w.id === opened.id)
          ? list
          : [...list, opened],
      );
      void qc.invalidateQueries({ queryKey: [...worksheetsKey(ws), "browse"] });
      await navigate({
        to: "/$ws/worksheets",
        params: { ws },
        search: { tab: opened.id },
      });
    },
    [ws, qc, navigate],
  );
}
