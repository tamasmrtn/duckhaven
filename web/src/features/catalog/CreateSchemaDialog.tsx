import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { useCreateSchema } from "@/queries/schemas.mutations";

export function CreateSchemaDialog({
  ws,
  catalog,
  open,
  onOpenChange,
}: {
  ws: string;
  catalog?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = useCreateSchema(ws, catalog);

  function reset() {
    setName("");
    setError(null);
  }

  async function handleCreate() {
    if (!name.trim()) {
      setError("Name is required");
      return;
    }
    try {
      await create.mutateAsync(name.trim());
      reset();
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create schema");
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) reset();
        onOpenChange(v);
      }}
    >
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>New schema</DialogTitle>
          <DialogDescription>
            Create a new schema (namespace) in this workspace's catalog.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="schema-name">Name</Label>
            <Input
              id="schema-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="analytics"
              autoFocus
            />
          </div>
          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleCreate} disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
