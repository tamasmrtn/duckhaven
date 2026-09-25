import { useState } from "react";
import { Link2 } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { Worksheet } from "@/types/worksheet";
import { cn } from "@/utils";

interface WorksheetTabsProps {
  worksheets: Worksheet[];
  activeId: string | undefined;
  // Linked worksheets whose SQL differs from the saved query they save into.
  unsavedIds: Set<string>;
  onSelect: (id: string) => void;
  onClose: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onNew: () => void;
}

/** The worksheet tab strip: one tab per open worksheet, plus "+". */
export function WorksheetTabs({
  worksheets,
  activeId,
  unsavedIds,
  onSelect,
  onClose,
  onRename,
  onNew,
}: WorksheetTabsProps) {
  // Inline rename (double-click a tab): the tab being edited and its draft.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  function commit() {
    const id = editingId;
    setEditingId(null);
    if (id && draft.trim()) onRename(id, draft);
  }

  return (
    <div className="flex h-9 items-center gap-1 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 overflow-x-auto shrink-0">
      <Tabs value={activeId} onValueChange={onSelect}>
        <TabsList className="h-7 bg-transparent gap-0.5 p-0">
          {worksheets.map((sheet) => {
            const active = sheet.id === activeId;
            return (
              <TabsTrigger
                key={sheet.id}
                value={sheet.id}
                asChild
                onDoubleClick={() => {
                  setEditingId(sheet.id);
                  setDraft(sheet.title);
                }}
                className="group h-7 max-w-[220px] gap-1.5 rounded-t-sm rounded-b-none border-b-2 px-3 text-xs data-[state=active]:border-[var(--brand-yellow)] data-[state=active]:bg-[var(--bg-canvas)] data-[state=inactive]:border-transparent"
              >
                {/* A <div> here (not a <button>) because the close control
                    below is itself interactive — nesting a <button> inside a
                    <button role="tab"> is invalid HTML and breaks keyboard/AT
                    semantics. Radix's asChild merges role="tab", aria-selected,
                    data-state, and roving-tabindex/keyboard handling onto this
                    div exactly as it would a native trigger button. */}
                <div className="cursor-pointer" title={sheet.title}>
                  {unsavedIds.has(sheet.id) && editingId !== sheet.id && (
                    <span
                      className="size-1.5 shrink-0 rounded-full bg-[var(--brand-orange)]"
                      aria-label="unsaved changes to saved query"
                    />
                  )}
                  {sheet.saved_query_id && !unsavedIds.has(sheet.id) && (
                    <Link2
                      className="size-3 shrink-0 text-text-tertiary"
                      aria-label="saved query"
                    />
                  )}
                  {editingId === sheet.id ? (
                    <input
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      onBlur={commit}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commit();
                        else if (e.key === "Escape") setEditingId(null);
                        e.stopPropagation();
                      }}
                      // Don't let clicks bubble to the tab trigger while editing.
                      onClick={(e) => e.stopPropagation()}
                      autoFocus
                      aria-label="Rename worksheet"
                      className="w-28 bg-transparent text-xs outline-none border-b border-[var(--brand-yellow)]"
                    />
                  ) : (
                    <span className="truncate">{sheet.title}</span>
                  )}
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onClose(sheet.id);
                    }}
                    className={cn(
                      "ml-0.5 shrink-0 rounded px-0.5 opacity-60 hover:opacity-100",
                      active ? "inline-flex" : "hidden group-hover:inline-flex",
                    )}
                    aria-label={`Close ${sheet.title}`}
                  >
                    ×
                  </button>
                </div>
              </TabsTrigger>
            );
          })}
        </TabsList>
      </Tabs>
      <button
        type="button"
        onClick={onNew}
        className="ml-1 flex size-6 shrink-0 items-center justify-center rounded text-text-secondary hover:bg-accent hover:text-text-primary text-sm"
        aria-label="New worksheet"
      >
        +
      </button>
    </div>
  );
}
