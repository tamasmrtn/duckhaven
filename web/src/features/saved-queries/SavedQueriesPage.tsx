import { useState } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import {
  BookMarked,
  Clock,
  ExternalLink,
  Pencil,
  Trash2,
  User,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
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
import { stashWorksheetQuery } from "@/features/catalog/worksheetSql";
import type { SavedQuery } from "@/types/saved-query";

export function SavedQueriesPage() {
  const { ws } = useParams({ from: "/$ws/saved-queries" });
  const navigate = useNavigate();
  const { data: queries = [], isLoading } = useSavedQueries(ws);
  const [renaming, setRenaming] = useState<SavedQuery | null>(null);
  const [deleting, setDeleting] = useState<SavedQuery | null>(null);

  // Worksheet hand-off: stash the SQL (plus the saved agent and id) and
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
      <PageHeader title="Saved queries" />

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
