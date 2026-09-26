import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { FileCode2, Link2, MoreHorizontal, Search, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useSavedQueries } from "@/queries/queries";
import { useWorksheetBrowser } from "@/queries/worksheets";
import type { SavedQuery } from "@/types/saved-query";
import type { Worksheet } from "@/types/worksheet";
import { cn } from "@/utils";
import { relativeTime } from "@/utils/relativeTime";

interface WorksheetBrowserProps {
  ws: string;
  openIds: Set<string>;
  activeId: string | undefined;
  onOpen: (id: string) => void;
  onOpenSaved: (query: SavedQuery) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => Promise<void>;
}

/**
 * Every worksheet the user has, open or closed, and the workspace's shared
 * saved queries — a Snowsight-style list beside the editor, so closing a tab
 * never means losing track of it.
 */
export function WorksheetBrowser({
  ws,
  openIds,
  activeId,
  onOpen,
  onOpenSaved,
  onRename,
  onDelete,
}: WorksheetBrowserProps) {
  const [search, setSearch] = useState("");
  const q = useDebouncedValue(search.trim(), 250);
  const sheets = useWorksheetBrowser(ws, q);
  const { data: saved = [] } = useSavedQueries(ws);
  const [renaming, setRenaming] = useState<Worksheet | null>(null);
  const [deleting, setDeleting] = useState<Worksheet | null>(null);

  const worksheets = sheets.data?.pages.flatMap((p) => p.items) ?? [];
  const needle = q.toLowerCase();
  const shared = saved
    .filter((s) => !needle || s.name.toLowerCase().includes(needle))
    .sort((a, b) => a.name.localeCompare(b.name));

  return (
    <div className="flex h-full flex-col">
      <div className="p-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-text-tertiary" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search worksheets…"
            aria-label="Search worksheets"
            className="h-8 pl-7 text-xs"
          />
        </div>
      </div>
      <div className="flex-1 overflow-auto px-1 pb-2">
        <SectionHeading>My worksheets</SectionHeading>
        {sheets.isLoading ? (
          <p className="px-3 py-2 text-xs text-text-tertiary">Loading…</p>
        ) : worksheets.length === 0 ? (
          <p className="px-3 py-2 text-xs text-text-tertiary">
            {q ? "No worksheets match." : "No worksheets yet."}
          </p>
        ) : (
          <ul aria-label="My worksheets">
            {worksheets.map((sheet) => (
              <li key={sheet.id} className="group flex items-center">
                <button
                  type="button"
                  onClick={() => onOpen(sheet.id)}
                  className={cn(
                    "flex min-w-0 flex-1 items-center gap-2 rounded px-2 py-1 text-left text-xs hover:bg-accent",
                    sheet.id === activeId && "bg-accent",
                  )}
                  title={sheet.title}
                >
                  <FileCode2 className="size-3.5 shrink-0 text-text-tertiary" />
                  <span className="truncate text-text-primary">
                    {sheet.title}
                  </span>
                  {sheet.saved_query_id && (
                    <Link2
                      className="size-3 shrink-0 text-text-tertiary"
                      aria-label="linked to a saved query"
                    />
                  )}
                  {openIds.has(sheet.id) && (
                    <span
                      className="size-1.5 shrink-0 rounded-full bg-[var(--brand-yellow)]"
                      aria-label="open"
                      title="Open in a tab"
                    />
                  )}
                  <span className="ml-auto shrink-0 text-2xs text-text-tertiary">
                    {relativeTime(sheet.updated_at)}
                  </span>
                </button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button
                      type="button"
                      aria-label={`Actions for ${sheet.title}`}
                      className="mr-1 shrink-0 rounded p-0.5 text-text-tertiary opacity-0 hover:text-text-primary focus:opacity-100 group-hover:opacity-100"
                    >
                      <MoreHorizontal className="size-3.5" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onSelect={() => onOpen(sheet.id)}>
                      Open
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => setRenaming(sheet)}>
                      Rename…
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() => setDeleting(sheet)}
                      className="text-[var(--status-failed)]"
                    >
                      Delete…
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </li>
            ))}
          </ul>
        )}
        {sheets.hasNextPage && (
          <button
            type="button"
            onClick={() => void sheets.fetchNextPage()}
            className="px-3 py-1 text-2xs text-text-secondary hover:text-text-primary"
          >
            Load more
          </button>
        )}

        <SectionHeading>Shared saved queries</SectionHeading>
        {shared.length === 0 ? (
          <p className="px-3 py-2 text-xs text-text-tertiary">
            {q
              ? "No saved queries match."
              : "Nothing saved in this workspace yet."}
          </p>
        ) : (
          <ul aria-label="Shared saved queries">
            {shared.map((query) => (
              <li key={query.id}>
                <button
                  type="button"
                  onClick={() => onOpenSaved(query)}
                  className="flex w-full min-w-0 items-center gap-2 rounded px-2 py-1 text-left text-xs hover:bg-accent"
                  title={query.name}
                >
                  <Users className="size-3.5 shrink-0 text-text-tertiary" />
                  <span className="truncate text-text-primary">
                    {query.name}
                  </span>
                  {query.created_by_name && (
                    <span className="ml-auto shrink-0 text-2xs text-text-tertiary">
                      {query.created_by_name}
                    </span>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="border-t border-[var(--border-subtle)] px-3 py-2">
        <Link
          to="/$ws/saved-queries"
          params={{ ws }}
          className="text-2xs font-medium text-[var(--brand-slate-blue)] hover:underline"
        >
          Manage saved queries ↗
        </Link>
      </div>

      <RenameDialog
        sheet={renaming}
        onClose={() => setRenaming(null)}
        onRename={(title) => {
          if (renaming) onRename(renaming.id, title);
          setRenaming(null);
        }}
      />
      <Dialog
        open={deleting !== null}
        onOpenChange={(v) => !v && setDeleting(null)}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Delete worksheet</DialogTitle>
            <DialogDescription>
              Permanently delete “{deleting?.title}”. A saved query it was
              linked to is not affected.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (deleting) void onDelete(deleting.id);
                setDeleting(null);
              }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="px-2 pb-1 pt-3 text-2xs font-semibold uppercase tracking-wide text-text-tertiary">
      {children}
    </h3>
  );
}

function RenameDialog({
  sheet,
  onClose,
  onRename,
}: {
  sheet: Worksheet | null;
  onClose: () => void;
  onRename: (title: string) => void;
}) {
  return (
    <Dialog open={sheet !== null} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-sm">
        {sheet && (
          <RenameForm
            key={sheet.id}
            initial={sheet.title}
            onCancel={onClose}
            onRename={onRename}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function RenameForm({
  initial,
  onCancel,
  onRename,
}: {
  initial: string;
  onCancel: () => void;
  onRename: (title: string) => void;
}) {
  const [title, setTitle] = useState(initial);
  return (
    <>
      <DialogHeader>
        <DialogTitle>Rename worksheet</DialogTitle>
      </DialogHeader>
      <Input
        aria-label="Worksheet name"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && title.trim() && onRename(title)}
        autoFocus
      />
      <DialogFooter>
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button disabled={!title.trim()} onClick={() => onRename(title)}>
          Rename
        </Button>
      </DialogFooter>
    </>
  );
}
