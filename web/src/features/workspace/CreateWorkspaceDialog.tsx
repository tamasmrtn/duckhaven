import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCreateWorkspace } from "@/queries/workspaces";

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function CreateWorkspaceDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (slug: string) => void;
}) {
  const createWorkspace = useCreateWorkspace();
  const [wsName, setWsName] = useState("");
  const [error, setError] = useState("");

  const slug = slugify(wsName);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const ws = await createWorkspace.mutateAsync({ slug, name: wsName });
      onCreated(ws.slug);
    } catch {
      setError("Could not create workspace. Check the details and try again.");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Create workspace</DialogTitle>
          <DialogDescription>
            Name your workspace. It starts empty — create or attach a catalog
            afterward to choose its storage and start querying.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 py-1">
          <div className="space-y-1.5">
            <Label htmlFor="ws-name">Workspace name</Label>
            <Input
              id="ws-name"
              value={wsName}
              onChange={(e) => setWsName(e.target.value)}
              placeholder="Analytics"
              required
              className="h-9"
            />
            {slug && (
              <p className="text-2xs text-text-tertiary">
                Slug: <span className="font-mono">{slug}</span>
              </p>
            )}
          </div>

          {error && (
            <p className="text-xs text-[var(--status-failed)]" role="alert">
              {error}
            </p>
          )}

          <DialogFooter>
            <Button type="submit" disabled={createWorkspace.isPending || !slug}>
              {createWorkspace.isPending ? "Creating…" : "Create workspace"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
