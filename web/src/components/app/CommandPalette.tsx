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
import { FileText, BookOpen, Database, Clock, Settings } from "lucide-react";
import { useWorkspaces } from "@/queries/workspaces";
import { StorageIcon } from "./StorageIcon";

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

  function go(to: string, params?: Record<string, string>) {
    onClose();
    void navigate({ to, params } as Parameters<typeof navigate>[0]);
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
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
        <Command className="rounded-lg">
          <CommandInput
            placeholder="Search commands, workspaces, tables…"
            className="h-11"
            autoFocus
          />
          <CommandList className="max-h-[400px]">
            <CommandEmpty>No results found.</CommandEmpty>

            {workspaces.length > 0 && (
              <CommandGroup heading="Workspaces">
                {workspaces.map((ws) => (
                  <CommandItem
                    key={ws.id}
                    value={`workspace ${ws.name}`}
                    onSelect={() => go("/$ws/worksheets", { ws: ws.slug })}
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

            {currentWs && (
              <>
                <CommandSeparator />
                <CommandGroup heading="Navigate">
                  <CommandItem
                    value="worksheets"
                    onSelect={() => go("/$ws/worksheets", { ws: currentWs })}
                    className="gap-2"
                  >
                    <FileText className="size-4 text-text-secondary" />
                    Worksheets
                  </CommandItem>
                  <CommandItem
                    value="catalog"
                    onSelect={() => go("/$ws/catalog", { ws: currentWs })}
                    className="gap-2"
                  >
                    <BookOpen className="size-4 text-text-secondary" />
                    Catalog
                  </CommandItem>
                  <CommandItem
                    value="saved queries"
                    onSelect={() => go("/$ws/saved-queries", { ws: currentWs })}
                    className="gap-2"
                  >
                    <Database className="size-4 text-text-secondary" />
                    Saved queries
                  </CommandItem>
                  <CommandItem
                    value="history"
                    onSelect={() => go("/$ws/history", { ws: currentWs })}
                    className="gap-2"
                  >
                    <Clock className="size-4 text-text-secondary" />
                    History
                  </CommandItem>
                  <CommandItem
                    value="admin"
                    onSelect={() => go("/$ws/admin/agents", { ws: currentWs })}
                    className="gap-2"
                  >
                    <Settings className="size-4 text-text-secondary" />
                    Admin
                  </CommandItem>
                </CommandGroup>
              </>
            )}
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
