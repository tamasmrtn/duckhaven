import { useState } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import {
  BookMarked,
  CalendarClock,
  Clock,
  ExternalLink,
  Pencil,
  Trash2,
  User,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useSavedQueries,
  useUpdateSavedQuery,
  useDeleteSavedQuery,
} from "@/queries/queries";
import {
  useSchedules,
  useCreateSchedule,
  useUpdateSchedule,
  useDeleteSchedule,
  useScheduleRuns,
} from "@/queries/schedules";
import { AgentPicker } from "@/components/app/AgentPicker";
import { StatusPill } from "@/components/app/StatusPill";
import { stashWorksheetQuery } from "@/features/catalog/worksheetSql";
import type { SavedQuery } from "@/types/saved-query";
import type { Schedule } from "@/types/schedule";

export function SavedQueriesPage() {
  const { ws } = useParams({ from: "/$ws/saved-queries" });
  const navigate = useNavigate();
  const { data: queries = [], isLoading } = useSavedQueries(ws);
  const { data: schedules = [] } = useSchedules(ws);
  const [renaming, setRenaming] = useState<SavedQuery | null>(null);
  const [deleting, setDeleting] = useState<SavedQuery | null>(null);
  const [scheduling, setScheduling] = useState<SavedQuery | null>(null);

  // One schedule per saved query in v1: index by saved_query_id for the cards.
  const scheduleFor = new Map(
    schedules.map((s) => [s.saved_query_id, s] as const),
  );

  // Snowsight-style hand-off: stash the SQL (plus the saved agent and id) and
  // navigate to the worksheet, which seeds a new tab from the stash on mount.
  function openInWorksheet(q: SavedQuery) {
    stashWorksheetQuery(ws, {
      sql: q.sql,
      agentId: q.default_agent_id ?? undefined,
      savedQueryId: q.id,
    });
    navigate({ to: "/$ws/worksheets", params: { ws } });
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <h1 className="text-md font-semibold">Saved queries</h1>
      </div>

      <div className="flex-1 overflow-auto p-6">
        {isLoading ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-32 animate-shimmer rounded-md" />
            ))}
          </div>
        ) : queries.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <BookMarked className="size-8 text-text-tertiary" />
            <p className="text-md font-medium text-text-secondary">
              Save a worksheet to keep it here.
            </p>
            <p className="text-sm text-text-tertiary">
              Click "Save…" in the worksheet editor to name and save your query.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {queries.map((q) => (
              <div
                key={q.id}
                data-testid={`sq-card-${q.id}`}
                className="flex flex-col gap-2 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4 shadow-e1 hover:shadow-e2 transition-shadow"
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="font-medium text-sm text-text-primary">
                    {q.name}
                  </p>
                  <div className="flex shrink-0 items-center gap-0.5">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      aria-label={`Schedule ${q.name}`}
                      onClick={() => setScheduling(q)}
                    >
                      <CalendarClock className="size-3" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      aria-label={`Rename ${q.name}`}
                      onClick={() => setRenaming(q)}
                    >
                      <Pencil className="size-3" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-6"
                      aria-label={`Delete ${q.name}`}
                      onClick={() => setDeleting(q)}
                    >
                      <Trash2 className="size-3" />
                    </Button>
                  </div>
                </div>
                <pre className="flex-1 truncate whitespace-pre-wrap font-mono text-xs text-[var(--text-code)] bg-[var(--bg-code)] rounded px-2 py-1.5 max-h-16 overflow-hidden">
                  {q.sql}
                </pre>
                <div className="flex items-center justify-between gap-2">
                  <div className="flex flex-col gap-1 text-2xs text-text-tertiary">
                    {q.created_by_name && (
                      <span className="flex items-center gap-1.5">
                        <User className="size-3" />
                        Saved by {q.created_by_name}
                      </span>
                    )}
                    {q.last_run_at && (
                      <span className="flex items-center gap-1.5">
                        <Clock className="size-3" />
                        Last run {new Date(q.last_run_at).toLocaleDateString()}
                      </span>
                    )}
                    {(() => {
                      const sched = scheduleFor.get(q.id);
                      if (!sched?.enabled || !sched.next_run_at) return null;
                      return (
                        <span className="flex items-center gap-1.5 text-[var(--status-running)]">
                          <CalendarClock className="size-3" />
                          Next run{" "}
                          {new Date(sched.next_run_at).toLocaleString()}
                        </span>
                      );
                    })()}
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 shrink-0 gap-1.5 text-xs"
                    onClick={() => openInWorksheet(q)}
                  >
                    <ExternalLink className="size-3" />
                    Open
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <RenameDialog
        ws={ws}
        query={renaming}
        onClose={() => setRenaming(null)}
      />
      <DeleteDialog
        ws={ws}
        query={deleting}
        onClose={() => setDeleting(null)}
      />
      <ScheduleDialog
        ws={ws}
        query={scheduling}
        schedule={scheduling ? (scheduleFor.get(scheduling.id) ?? null) : null}
        onClose={() => setScheduling(null)}
      />
    </div>
  );
}

function RenameDialog({
  ws,
  query,
  onClose,
}: {
  ws: string;
  query: SavedQuery | null;
  onClose: () => void;
}) {
  const update = useUpdateSavedQuery(ws);
  const [name, setName] = useState("");

  // Seed the input each time a different query opens the dialog.
  const [seededId, setSeededId] = useState<string | null>(null);
  if (query && query.id !== seededId) {
    setSeededId(query.id);
    setName(query.name);
  }

  async function handleRename() {
    if (!query || !name.trim()) return;
    await update.mutateAsync({ id: query.id, data: { name: name.trim() } });
    onClose();
  }

  return (
    <Dialog open={query !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Rename query</DialogTitle>
          <DialogDescription>
            Give this saved query a new name.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2 py-2">
          <Label htmlFor="rename-name" className="text-sm">
            Name
          </Label>
          <Input
            id="rename-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
            onKeyDown={(e) => e.key === "Enter" && handleRename()}
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={handleRename}
            disabled={!name.trim() || update.isPending}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Five space-separated cron-ish fields. Mirrors the backend's croniter check
// well enough to gate the Save button; the server is the source of truth (422).
function cronLooksValid(expr: string): boolean {
  const fields = expr.trim().split(/\s+/);
  return fields.length === 5 && fields.every((f) => /^[\d*/,-]+$/.test(f));
}

function ScheduleDialog({
  ws,
  query,
  schedule,
  onClose,
}: {
  ws: string;
  query: SavedQuery | null;
  schedule: Schedule | null;
  onClose: () => void;
}) {
  const create = useCreateSchedule(ws);
  const update = useUpdateSchedule(ws);
  const remove = useDeleteSchedule(ws);
  const { data: runs = [] } = useScheduleRuns(ws, schedule?.id ?? null);

  const [cron, setCron] = useState("0 2 * * *");
  const [enabled, setEnabled] = useState(true);
  const [agentId, setAgentId] = useState<string | null>(null);

  // Seed the form each time a different saved query opens the dialog.
  const [seededId, setSeededId] = useState<string | null>(null);
  if (query && query.id !== seededId) {
    setSeededId(query.id);
    setCron(schedule?.cron ?? "0 2 * * *");
    setEnabled(schedule?.enabled ?? true);
    setAgentId(schedule?.agent_id ?? query.default_agent_id ?? null);
  }

  const valid = cronLooksValid(cron);
  const pending = create.isPending || update.isPending;

  async function handleSave() {
    if (!query || !valid) return;
    const data = { cron: cron.trim(), enabled, agent_id: agentId };
    if (schedule) {
      await update.mutateAsync({ id: schedule.id, data });
    } else {
      await create.mutateAsync({ saved_query_id: query.id, ...data });
    }
    onClose();
  }

  async function handleRemove() {
    if (!schedule) return;
    await remove.mutateAsync(schedule.id);
    onClose();
  }

  return (
    <Dialog open={query !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Schedule "{query?.name}"</DialogTitle>
          <DialogDescription>
            Run this saved query automatically on a cron schedule (UTC).
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="schedule-cron" className="text-sm">
              Cron expression
            </Label>
            <Input
              id="schedule-cron"
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              placeholder="0 2 * * *"
              aria-invalid={!valid}
            />
            <p
              className={
                valid
                  ? "text-2xs text-text-tertiary"
                  : "text-2xs text-[var(--status-failed)]"
              }
            >
              {valid
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
              The chosen agent runs each scheduled execution. If it is offline
              at run time, the run is recorded as failed.
            </p>
          </div>

          {schedule && (
            <div className="space-y-1.5">
              <Label className="text-sm">Run history</Label>
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

        <DialogFooter className="sm:justify-between">
          {schedule ? (
            <Button
              variant="destructive"
              onClick={handleRemove}
              disabled={remove.isPending}
            >
              Remove schedule
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={!valid || pending}>
              {schedule ? "Save" : "Schedule"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DeleteDialog({
  ws,
  query,
  onClose,
}: {
  ws: string;
  query: SavedQuery | null;
  onClose: () => void;
}) {
  const remove = useDeleteSavedQuery(ws);

  async function handleDelete() {
    if (!query) return;
    await remove.mutateAsync(query.id);
    onClose();
  }

  return (
    <Dialog open={query !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Delete query</DialogTitle>
          <DialogDescription>
            Permanently delete "{query?.name}". This cannot be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={handleDelete}
            disabled={remove.isPending}
          >
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
