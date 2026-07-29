import { useState } from "react";
import { useParams } from "@tanstack/react-router";
import { CalendarClock, Plus } from "lucide-react";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { AgentPicker } from "@/components/app/AgentPicker";
import { StatusPill } from "@/components/app/StatusPill";
import { useSavedQueries } from "@/queries/queries";
import { useAgents } from "@/queries/agents";
import {
  useSchedules,
  useCreateSchedule,
  useUpdateSchedule,
  useDeleteSchedule,
  useScheduleRuns,
  useAllScheduleRuns,
} from "@/queries/schedules";
import type { Schedule } from "@/types/schedule";
import { cn, shortId } from "@/utils";

// Five space-separated cron-ish fields. Mirrors the backend's croniter check
// well enough to gate the Save button; the server is the source of truth (422).
function cronLooksValid(expr: string): boolean {
  const fields = expr.trim().split(/\s+/);
  return fields.length === 5 && fields.every((f) => /^[\d*/,-]+$/.test(f));
}

function formatWhen(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function SchedulesPage() {
  const { ws } = useParams({ from: "/$ws/schedules" });
  const { data: schedules = [], isLoading } = useSchedules(ws);
  const { data: savedQueries = [] } = useSavedQueries(ws);
  const { data: agents = [] } = useAgents();

  const savedName = new Map(savedQueries.map((q) => [q.id, q.name] as const));
  const agentName = new Map(agents.map((a) => [a.id, a.name] as const));
  const scheduleName = (s: Schedule) =>
    (s.saved_query_id && savedName.get(s.saved_query_id)) || s.job_type;
  // For the runs feed: schedule_id -> human label.
  const labelByScheduleId = new Map(
    schedules.map((s) => [s.id, scheduleName(s)] as const),
  );

  const [editing, setEditing] = useState<Schedule | null>(null);
  const [creating, setCreating] = useState(false);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <h1 className="text-md font-semibold">Schedules</h1>
      </div>

      <Tabs defaultValue="schedules" className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center justify-between px-6 pt-3">
          <TabsList>
            <TabsTrigger value="schedules">Schedules</TabsTrigger>
            <TabsTrigger value="runs">Runs</TabsTrigger>
          </TabsList>
          <Button
            size="sm"
            className="h-8 gap-1.5"
            onClick={() => setCreating(true)}
          >
            <Plus className="size-3.5" />
            New schedule
          </Button>
        </div>

        <TabsContent
          value="schedules"
          className="mt-3 min-h-0 flex-1 overflow-auto px-6 pb-6"
        >
          {isLoading ? (
            <div className="space-y-1">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton
                  key={i}
                  className="h-12 w-full animate-shimmer rounded"
                />
              ))}
            </div>
          ) : schedules.length === 0 ? (
            <EmptyState
              title="No schedules yet."
              subtitle="Create a schedule to run a saved query on a cron cadence."
            />
          ) : (
            <Table containerClassName="overflow-visible" className="text-sm">
              <TableHeader className="sticky top-0 bg-[var(--bg-surface)] z-10">
                <TableRow className="border-b border-[var(--border-subtle)] hover:bg-transparent">
                  {[
                    "Query",
                    "Cron",
                    "Agent",
                    "Status",
                    "Next run",
                    "Last run",
                  ].map((h) => (
                    <TableHead
                      key={h}
                      className="h-auto px-3 py-2 text-left text-xs font-medium text-text-secondary"
                    >
                      {h}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {schedules.map((s, i) => (
                  <TableRow
                    key={s.id}
                    onClick={() => setEditing(s)}
                    className={cn(
                      "cursor-pointer border-b border-[var(--border-subtle)] hover:bg-accent/50",
                      i % 2 === 0 ? "" : "bg-[var(--bg-surface)]/40",
                    )}
                  >
                    <TableCell className="px-3 py-2 font-medium text-text-primary">
                      {scheduleName(s)}
                    </TableCell>
                    <TableCell className="px-3 py-2 font-mono text-xs text-text-secondary">
                      {s.cron}
                    </TableCell>
                    <TableCell className="px-3 py-2 text-xs text-text-secondary">
                      {s.agent_id
                        ? (agentName.get(s.agent_id) ?? shortId(s.agent_id))
                        : "Auto"}
                    </TableCell>
                    <TableCell className="px-3 py-2">
                      <span
                        className={cn(
                          "rounded-full px-2 py-0.5 text-2xs font-medium",
                          s.enabled
                            ? "bg-[var(--status-success)] text-white"
                            : "bg-[var(--status-cancelled)] text-white",
                        )}
                      >
                        {s.enabled ? "enabled" : "disabled"}
                      </span>
                    </TableCell>
                    <TableCell className="px-3 py-2 font-mono text-2xs text-text-tertiary">
                      {s.enabled ? formatWhen(s.next_run_at) : "—"}
                    </TableCell>
                    <TableCell className="px-3 py-2 font-mono text-2xs text-text-tertiary">
                      {formatWhen(s.last_run_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </TabsContent>

        <TabsContent value="runs" className="mt-3 min-h-0 flex-1 overflow-auto">
          <RunsTab
            ws={ws}
            labelByScheduleId={labelByScheduleId}
            agentName={agentName}
          />
        </TabsContent>
      </Tabs>

      <ScheduleDialog
        ws={ws}
        open={creating || editing !== null}
        schedule={editing}
        savedQueries={savedQueries}
        onClose={() => {
          setCreating(false);
          setEditing(null);
        }}
      />
    </div>
  );
}

function EmptyState({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      <CalendarClock className="size-8 text-text-tertiary" />
      <p className="text-md font-medium text-text-secondary">{title}</p>
      <p className="text-sm text-text-tertiary">{subtitle}</p>
    </div>
  );
}

function RunsTab({
  ws,
  labelByScheduleId,
  agentName,
}: {
  ws: string;
  labelByScheduleId: Map<string, string>;
  agentName: Map<string, string>;
}) {
  const { data: runs = [], isLoading } = useAllScheduleRuns(ws);

  if (isLoading) {
    return (
      <div className="space-y-1 px-6">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full animate-shimmer rounded" />
        ))}
      </div>
    );
  }
  if (runs.length === 0) {
    return (
      <EmptyState
        title="No scheduled runs yet."
        subtitle="Runs from your schedules will appear here, newest first."
      />
    );
  }

  return (
    <Table containerClassName="overflow-visible" className="text-sm">
      <TableHeader className="sticky top-0 bg-[var(--bg-surface)] z-10">
        <TableRow className="border-b border-[var(--border-subtle)] hover:bg-transparent">
          {["Status", "Schedule", "Agent", "Rows", "Duration", "Started"].map(
            (h) => (
              <TableHead
                key={h}
                className="h-auto px-4 py-2 text-left text-xs font-medium text-text-secondary"
              >
                {h}
              </TableHead>
            ),
          )}
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((r, i) => (
          <TableRow
            key={r.id}
            className={cn(
              "border-b border-[var(--border-subtle)] hover:bg-transparent",
              i % 2 === 0 ? "" : "bg-[var(--bg-surface)]/40",
            )}
          >
            <TableCell className="px-4 py-2">
              <StatusPill status={r.status} durationMs={r.duration_ms} />
            </TableCell>
            <TableCell className="px-4 py-2 text-xs text-text-primary">
              {(r.schedule_id && labelByScheduleId.get(r.schedule_id)) ?? "—"}
            </TableCell>
            <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary">
              {r.agent_id
                ? (agentName.get(r.agent_id) ?? shortId(r.agent_id))
                : "—"}
              {r.error && (
                <p className="mt-0.5 text-2xs text-[var(--status-failed)] truncate max-w-xs">
                  {r.error}
                </p>
              )}
            </TableCell>
            <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary font-tabular">
              {r.row_count != null ? r.row_count.toLocaleString() : "—"}
            </TableCell>
            <TableCell className="px-4 py-2 font-mono text-xs text-text-secondary font-tabular">
              {formatDuration(r.duration_ms)}
            </TableCell>
            <TableCell className="px-4 py-2 font-mono text-2xs text-text-tertiary">
              {new Date(r.started_at).toLocaleString()}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function ScheduleDialog({
  ws,
  open,
  schedule,
  savedQueries,
  onClose,
}: {
  ws: string;
  open: boolean;
  schedule: Schedule | null;
  savedQueries: {
    id: string;
    name: string;
    default_agent_id?: string | null;
  }[];
  onClose: () => void;
}) {
  const create = useCreateSchedule(ws);
  const update = useUpdateSchedule(ws);
  const remove = useDeleteSchedule(ws);
  const { data: runs = [] } = useScheduleRuns(ws, schedule?.id ?? null);

  const [savedQueryId, setSavedQueryId] = useState<string>("");
  const [cron, setCron] = useState("0 2 * * *");
  const [enabled, setEnabled] = useState(true);
  const [agentId, setAgentId] = useState<string | null>(null);

  // Re-seed the form whenever the dialog (re)opens for a different target.
  const [seedKey, setSeedKey] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const key = schedule ? `edit:${schedule.id}` : open ? "create" : null;
  if (key && key !== seedKey) {
    setSeedKey(key);
    if (schedule) {
      setSavedQueryId(schedule.saved_query_id ?? "");
      setCron(schedule.cron);
      setEnabled(schedule.enabled);
      setAgentId(schedule.agent_id ?? null);
    } else {
      const first = savedQueries[0];
      setSavedQueryId(first?.id ?? "");
      setCron("0 2 * * *");
      setEnabled(true);
      setAgentId(first?.default_agent_id ?? null);
    }
  }
  if (!open && seedKey !== null) setSeedKey(null);

  const valid =
    cronLooksValid(cron) && (schedule !== null || savedQueryId !== "");
  const pending = create.isPending || update.isPending;

  async function handleSave() {
    if (!valid) return;
    setSaveError(null);
    const data = { cron: cron.trim(), enabled, agent_id: agentId };
    try {
      if (schedule) {
        await update.mutateAsync({ id: schedule.id, data });
      } else {
        await create.mutateAsync({ saved_query_id: savedQueryId, ...data });
      }
    } catch (e) {
      // Chiefly the per-agent access check: the picker only offers agents you
      // may use, but a grant can be revoked while this dialog is open.
      setSaveError(
        e instanceof ApiError && (e.status === 403 || e.status === 404)
          ? "You no longer have access to the selected agent. Pick another one."
          : e instanceof Error
            ? e.message
            : "Could not save the schedule.",
      );
      return;
    }
    onClose();
  }

  async function handleRemove() {
    if (!schedule) return;
    await remove.mutateAsync(schedule.id);
    onClose();
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>
            {schedule ? "Edit schedule" : "New schedule"}
          </DialogTitle>
          <DialogDescription>
            Run a saved query automatically on a cron schedule (UTC).
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          <div className="space-y-1.5">
            <Label className="text-sm">Saved query</Label>
            {schedule ? (
              <Input
                value={
                  savedQueries.find((q) => q.id === savedQueryId)?.name ??
                  "(saved query)"
                }
                readOnly
                disabled
              />
            ) : savedQueries.length === 0 ? (
              <p className="text-2xs text-[var(--status-failed)]">
                Create a saved query first, then schedule it here.
              </p>
            ) : (
              <Select value={savedQueryId} onValueChange={setSavedQueryId}>
                <SelectTrigger aria-label="Saved query">
                  <SelectValue placeholder="Select a saved query…" />
                </SelectTrigger>
                <SelectContent>
                  {savedQueries.map((q) => (
                    <SelectItem key={q.id} value={q.id}>
                      {q.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="schedule-cron" className="text-sm">
              Cron expression
            </Label>
            <Input
              id="schedule-cron"
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              placeholder="0 2 * * *"
              aria-invalid={!cronLooksValid(cron)}
            />
            <p
              className={
                cronLooksValid(cron)
                  ? "text-2xs text-text-tertiary"
                  : "text-2xs text-[var(--status-failed)]"
              }
            >
              {cronLooksValid(cron)
                ? "Five fields: minute hour day-of-month month day-of-week. e.g. 0 2 * * * = daily at 02:00 UTC."
                : "Enter five space-separated cron fields."}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <input
              id="schedule-enabled"
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="size-4"
            />
            <Label htmlFor="schedule-enabled" className="text-sm">
              Enabled
            </Label>
          </div>

          <div className="space-y-1.5">
            <Label className="text-sm">Agent</Label>
            <AgentPicker value={agentId} onChange={setAgentId} />
            <p className="text-2xs text-text-tertiary">
              The chosen agent runs each execution. If it is offline at run
              time, the run is recorded as failed.
            </p>
          </div>

          {schedule && (
            <div className="space-y-1.5">
              <Label className="text-sm">Recent runs</Label>
              {runs.length === 0 ? (
                <p className="text-2xs text-text-tertiary">No runs yet.</p>
              ) : (
                <ul className="max-h-40 space-y-1 overflow-auto">
                  {runs.map((r) => (
                    <li
                      key={r.id}
                      className="flex items-center justify-between gap-2 rounded border border-[var(--border-subtle)] px-2 py-1"
                    >
                      <StatusPill
                        status={r.status}
                        startedAt={r.started_at}
                        durationMs={r.duration_ms}
                      />
                      <span className="font-mono text-2xs text-text-tertiary">
                        {new Date(r.started_at).toLocaleString()}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        {saveError && (
          <p role="alert" className="text-xs text-destructive">
            {saveError}
          </p>
        )}

        <DialogFooter className="sm:justify-between">
          {schedule ? (
            <Button
              variant="destructive"
              onClick={handleRemove}
              disabled={remove.isPending}
            >
              Remove
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={!valid || pending}>
              {schedule ? "Save" : "Create"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
