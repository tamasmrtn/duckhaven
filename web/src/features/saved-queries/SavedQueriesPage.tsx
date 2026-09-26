import { useMemo, useState } from "react";
import { useParams } from "@tanstack/react-router";
import {
  BookMarked,
  Clock,
  ExternalLink,
  Pencil,
  Search,
  Trash2,
  User,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader, PageToolbar } from "@/components/ui/page-header";
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
import { Segmented } from "@/components/ui/segmented";
import { useOpenInWorksheet } from "@/features/worksheet/openInWorksheet";
import type { SavedQuery } from "@/types/saved-query";
import { relativeTime } from "@/utils/relativeTime";

type SortKey = "updated" | "name" | "last_run";

function sortQueries(queries: SavedQuery[], key: SortKey): SavedQuery[] {
  const by = (v: string | null | undefined) => v ?? "";
  return [...queries].sort((a, b) =>
    key === "name"
      ? a.name.localeCompare(b.name)
      : key === "last_run"
        ? by(b.last_run_at).localeCompare(by(a.last_run_at))
        : by(b.updated_at).localeCompare(by(a.updated_at)),
  );
}

export function SavedQueriesPage() {
  const { ws } = useParams({ from: "/$ws/saved-queries" });
  const { data: all = [], isLoading } = useSavedQueries(ws);
  const [renaming, setRenaming] = useState<SavedQuery | null>(null);
  const [deleting, setDeleting] = useState<SavedQuery | null>(null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("updated");
  const openWorksheet = useOpenInWorksheet(ws);

  const queries = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const matched = needle
      ? all.filter(
          (q) =>
            q.name.toLowerCase().includes(needle) ||
            q.sql.toLowerCase().includes(needle),
        )
      : all;
    return sortQueries(matched, sort);
  }, [all, search, sort]);

  // Opens the saved query's worksheet, focusing one already linked to it.
  function openInWorksheet(q: SavedQuery) {
    void openWorksheet({
      sql: q.sql,
      title: q.name,
      savedQueryId: q.id,
      agentId: q.default_agent_id ?? null,
    });
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Saved queries"
        description="Shared with everyone in the workspace. Open one to edit it in a worksheet; Save there updates it here."
      />

      {all.length > 0 && (
        <PageToolbar>
          <div className="relative w-56">
            <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-text-tertiary" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name or SQL…"
              aria-label="Search saved queries"
              className="h-8 pl-7 text-xs"
            />
          </div>
          <Segmented
            label="Sort"
            value={sort}
            onChange={setSort}
            options={[
              { value: "updated", label: "Updated" },
              { value: "name", label: "Name" },
              { value: "last_run", label: "Last run" },
            ]}
          />
        </PageToolbar>
      )}

      <div className="flex-1 overflow-auto p-6">
        {isLoading ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-32 animate-shimmer rounded-md" />
            ))}
          </div>
        ) : all.length > 0 && queries.length === 0 ? (
          <p className="text-sm text-text-tertiary">
            No saved queries match “{search.trim()}”.
          </p>
        ) : queries.length === 0 ? (
          <EmptyState
            icon={BookMarked}
            title="Save a worksheet to keep it here"
            description='Click "Save…" in the worksheet editor to name and save your query.'
          />
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
                    {q.updated_at && (
                      <span
                        className="flex items-center gap-1.5"
                        title={new Date(q.updated_at).toLocaleString()}
                      >
                        <Pencil className="size-3" />
                        Updated {relativeTime(q.updated_at)}
                        {q.updated_by_name &&
                          q.updated_by_name !== q.created_by_name &&
                          ` by ${q.updated_by_name}`}
                      </span>
                    )}
                    {q.last_run_at && (
                      <span className="flex items-center gap-1.5">
                        <Clock className="size-3" />
                        Last run {relativeTime(q.last_run_at)}
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
  const [error, setError] = useState<string | null>(null);

  // Seed the input each time a different query opens the dialog.
  const [seededId, setSeededId] = useState<string | null>(null);
  if (query && query.id !== seededId) {
    setSeededId(query.id);
    setName(query.name);
    setError(null);
  }

  async function handleRename() {
    if (!query || !name.trim()) return;
    try {
      await update.mutateAsync({ id: query.id, data: { name: name.trim() } });
    } catch (err) {
      // Names are unique per workspace, ignoring case.
      setError(
        err instanceof Error ? err.message : "Couldn't rename the query.",
      );
      return;
    }
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
            onChange={(e) => {
              setName(e.target.value);
              setError(null);
            }}
            autoFocus
            onKeyDown={(e) => e.key === "Enter" && handleRename()}
          />
          {error && (
            <p role="alert" className="text-xs text-[var(--status-failed)]">
              {error}
            </p>
          )}
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
