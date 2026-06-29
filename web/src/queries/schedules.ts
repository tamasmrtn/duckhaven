import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  schedulesApi,
  type ScheduleCreate,
  type ScheduleUpdate,
} from "@/api/schedules";

export function useSchedules(ws: string) {
  return useQuery({
    queryKey: ["workspace", ws, "schedules"],
    queryFn: () => schedulesApi.list(ws),
    enabled: !!ws,
  });
}

export function useCreateSchedule(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: ScheduleCreate) => schedulesApi.create(ws, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "schedules"] });
    },
  });
}

export function useUpdateSchedule(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ScheduleUpdate }) =>
      schedulesApi.update(ws, id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "schedules"] });
    },
  });
}

export function useDeleteSchedule(ws: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => schedulesApi.remove(ws, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workspace", ws, "schedules"] });
    },
  });
}

export function useScheduleRuns(ws: string, id: string | null) {
  return useQuery({
    queryKey: ["workspace", ws, "schedules", id, "runs"],
    queryFn: () => schedulesApi.listRuns(ws, id!),
    enabled: !!ws && !!id,
    // Poll while the dialog is open so in-flight runs update.
    refetchInterval: 3000,
  });
}
