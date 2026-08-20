import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";
import {
  Table2,
  Layers,
  BookMarked as SavedQueryIcon,
  Clock,
} from "lucide-react";
import { useWorkspaces } from "@/queries/workspaces";
import { useMe } from "@/queries/auth";
import { useWorkspaceSearch } from "@/queries/search";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { getRecentlyViewed } from "@/utils/recentlyViewed";
import { objectPath } from "@/utils/objectPath";
import { stashWorksheetQuery } from "@/features/catalog/worksheetSql";
import { navItems } from "./navItems";
import { StorageIcon } from "./StorageIcon";
import type { SearchResult } from "@/types/search";

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  currentWs?: string;
}

export function CommandPalette({
  open,
  onClose,
  currentWs,
}: CommandPaletteProps) {
  const navigate = useNavigate();
  const { data: workspaces = [] } = useWorkspaces();
  const { data: me } = useMe();
  const isAdmin = (me?.permissions?.length ?? 0) > 0;

  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 200);
  const searching = query.trim().length >= 2;
  const { data: results = [] } = useWorkspaceSearch(
    currentWs ?? "",
    searching ? debouncedQuery : "",
  );

  // Reset on close (rather than on open) so this stays a plain event handler
  // instead of a setState-in-effect: every close path — Escape, outside
  // click, or selecting a result via go() below — clears the query, so the
  // next open always starts fresh.
  function close() {
    setQuery("");
    onClose();
  }

  function go(to: string) {
    close();
    void navigate({ to } as Parameters<typeof navigate>[0]);
  }

  function openSavedQuery(r: SearchResult) {
    if (!currentWs || !r.id || r.sql == null) return;
    stashWorksheetQuery(currentWs, {
      sql: r.sql,
      agentId: r.default_agent_id ?? undefined,
      savedQueryId: r.id,
    });
    go(`/${currentWs}/worksheets`);
  }

  const needle = query.trim().toLowerCase();
  const filteredWorkspaces = workspaces.filter(
    (ws) => !needle || ws.name.toLowerCase().includes(needle),
  );
  const filteredNavItems = navItems.filter(
    (item) =>
      (!item.requiresAdmin || isAdmin) &&
      (!needle || item.label.toLowerCase().includes(needle)),
  );

  const schemaResults = results.filter((r) => r.type === "schema");
  const tableResults = results.filter((r) => r.type === "table");
  const savedQueryResults = results.filter((r) => r.type === "saved_query");
  const recent = currentWs && !searching ? getRecentlyViewed(currentWs) : [];

  return (
    <Dialog open={open} onOpenChange={(v) => !v && close()}>
      <DialogContent
        className="p-0 gap-0 max-w-lg"
        aria-label="Command palette"
      >
        <DialogHeader className="sr-only">
          <DialogTitle>Command palette</DialogTitle>
          <DialogDescription>
            Search commands, workspaces, and tables.
          </DialogDescription>
        </DialogHeader>
        <Command className="rounded-lg" shouldFilter={false}>
          <CommandInput
            value={query}
            onValueChange={setQuery}
            placeholder="Search commands, workspaces, tables…"
            className="h-11"
            autoFocus
          />
          <CommandList className="max-h-[400px]">
            <CommandEmpty>No results found.</CommandEmpty>

            {recent.length > 0 && (
              <CommandGroup heading="Recent">
                {recent.map((r) => (
                  <CommandItem
                    key={`recent-${r.type}-${r.catalog}-${r.schema}-${r.name}`}
                    value={`recent ${r.name}`}
                    onSelect={() =>
                      go(
                        objectPath(currentWs!, {
                          type: r.type,
                          catalog: r.catalog,
                          schema_name: r.schema,
                          name: r.name,
                        }),
                      )
                    }
                    className="gap-2"
                  >
                    <Clock className="size-4 text-text-secondary" />
                    <span className="truncate">{r.name}</span>
                    <span className="ml-auto truncate text-2xs text-text-tertiary">
                      {r.catalog}.{r.schema}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}

            {schemaResults.length > 0 && (
              <CommandGroup heading="Schemas">
                {schemaResults.map((r) => (
                  <CommandItem
                    key={`schema-${r.catalog}-${r.name}`}
                    value={`schema ${r.name}`}
                    onSelect={() => go(objectPath(currentWs!, r))}
                    className="gap-2"
                  >
                    <Layers className="size-4 text-text-secondary" />
                    <span className="truncate">{r.name}</span>
                    <span className="ml-auto truncate text-2xs text-text-tertiary">
                      {r.catalog}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}

            {tableResults.length > 0 && (
              <CommandGroup heading="Tables">
                {tableResults.map((r) => (
                  <CommandItem
                    key={`table-${r.catalog}-${r.schema_name}-${r.name}`}
                    value={`table ${r.name}`}
                    onSelect={() => go(objectPath(currentWs!, r))}
                    className="gap-2"
                  >
                    <Table2 className="size-4 text-text-secondary" />
                    <span className="truncate">{r.name}</span>
                    <span className="ml-auto truncate text-2xs text-text-tertiary">
                      {r.catalog}.{r.schema_name}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}

            {savedQueryResults.length > 0 && (
              <CommandGroup heading="Saved queries">
                {savedQueryResults.map((r) => (
                  <CommandItem
                    key={`saved-query-${r.id}`}
                    value={`saved query ${r.name}`}
                    onSelect={() => openSavedQuery(r)}
                    className="gap-2"
                  >
                    <SavedQueryIcon className="size-4 text-text-secondary" />
                    <span className="truncate">{r.name}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}

            {filteredWorkspaces.length > 0 && (
              <CommandGroup heading="Workspaces">
                {filteredWorkspaces.map((ws) => (
                  <CommandItem
                    key={ws.id}
                    value={`workspace ${ws.name}`}
                    onSelect={() => go(`/${ws.slug}/worksheets`)}
                    className="gap-2"
                  >
                    <StorageIcon
                      kind={ws.storage_backend_kind}
                      className="text-text-secondary"
                    />
                    {ws.name}
                  </CommandItem>
                ))}
              </CommandGroup>
            )}

            {currentWs && filteredNavItems.length > 0 && (
              <>
                <CommandSeparator />
                <CommandGroup heading="Navigate">
                  {filteredNavItems.map((item) => (
                    <CommandItem
                      key={item.segment}
                      value={item.label.toLowerCase()}
                      onSelect={() => go(`/${currentWs}/${item.segment}`)}
                      className="gap-2"
                    >
                      <item.icon className="size-4 text-text-secondary" />
                      {item.label}
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            )}
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
