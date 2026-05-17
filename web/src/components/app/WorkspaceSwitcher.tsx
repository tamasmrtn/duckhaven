import { useNavigate } from "@tanstack/react-router";
import {
  Dialog,
  DialogContent,
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
} from "@/components/ui/command";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Plus } from "lucide-react";
import { useWorkspaces } from "@/queries/workspaces";
import { StorageIcon } from "./StorageIcon";
import type { WorkspaceMemberRole } from "@/types/workspace";
import { cn } from "@/utils";

interface WorkspaceSwitcherProps {
  open: boolean;
  onClose: () => void;
  currentWs?: string;
}

const roleColors: Record<WorkspaceMemberRole, string> = {
  owner: "bg-muted text-muted-foreground",
  writer: "bg-muted text-muted-foreground",
  reader: "bg-muted text-muted-foreground",
};

export function WorkspaceSwitcher({
  open,
  onClose,
  currentWs,
}: WorkspaceSwitcherProps) {
  const navigate = useNavigate();
  const { data: workspaces = [] } = useWorkspaces();

  function handleSelect(slug: string) {
    onClose();
    void navigate({ to: "/$ws/worksheets", params: { ws: slug } });
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        className="p-0 gap-0 max-w-md"
        aria-label="Switch workspace"
      >
        <DialogHeader className="sr-only">
          <DialogTitle>Switch workspace</DialogTitle>
        </DialogHeader>
        <Command className="rounded-lg">
          <CommandInput
            placeholder="Type to filter…"
            className="h-11"
            autoFocus
          />
          <CommandList className="max-h-[320px]">
            <CommandEmpty>No workspaces found.</CommandEmpty>
            <CommandGroup>
              {workspaces.map((ws) => (
                <CommandItem
                  key={ws.id}
                  value={ws.slug}
                  onSelect={handleSelect}
                  className={cn(
                    "flex items-center gap-3 py-2.5 cursor-pointer",
                    ws.slug === currentWs && "bg-accent",
                  )}
                >
                  <StorageIcon
                    kind={ws.storage_backend_kind}
                    className="text-text-secondary shrink-0"
                  />
                  <span className="flex-1 font-medium text-sm">{ws.name}</span>
                  <Badge
                    variant="outline"
                    className={cn("text-2xs h-5", roleColors["owner"])}
                  >
                    owner
                  </Badge>
                  <span className="text-2xs font-mono text-text-tertiary uppercase">
                    {ws.storage_backend_kind === "adls_gen2"
                      ? "ADLS"
                      : ws.storage_backend_kind === "local_fs"
                        ? "local"
                        : ws.storage_backend_kind.toUpperCase()}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
            <Separator />
            <CommandGroup>
              <CommandItem
                value="__create"
                onSelect={() => {
                  onClose();
                  // Navigate to workspace create flow
                }}
                className="gap-2 text-text-secondary"
              >
                <Plus className="size-4" />
                Create workspace…
              </CommandItem>
            </CommandGroup>
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
