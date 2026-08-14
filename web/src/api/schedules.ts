import { get, post, patch, del } from "./client";
import type { Schedule } from "@/types/schedule";
import type { Query } from "@/types/query";

export interface ScheduleCreate {
  saved_query_id: string;
  cron: string;
  enabled?: boolean;
  agent_id?: string | null;
  job_type?: string;
}

export interface ScheduleUpdate {
  cron?: string;
  enabled?: boolean;
  agent_id?: string | null;
}

export const schedulesApi = {
  list: (ws: string, savedQueryId?: string) => {
    const qs = savedQueryId ? `?saved_query_id=${savedQueryId}` : "";
    return get<Schedule[]>(`/workspaces/${ws}/schedules${qs}`);
  },

  create: (ws: string, data: ScheduleCreate) =>
    post<Schedule>(`/workspaces/${ws}/schedules`, data),

  update: (ws: string, id: string, data: ScheduleUpdate) =>
    patch<Schedule>(`/workspaces/${ws}/schedules/${id}`, data),

  remove: (ws: string, id: string) => del(`/workspaces/${ws}/schedules/${id}`),

  // Per-job run history (newest first).
  listRuns: (ws: string, id: string) =>
    get<Query[]>(`/workspaces/${ws}/schedules/${id}/runs`),

  // Every scheduled run in the workspace, newest first — the global runs feed.
  listAllRuns: (ws: string) => get<Query[]>(`/workspaces/${ws}/schedule-runs`),
};
