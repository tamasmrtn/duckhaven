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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCreateSchema } from "@/queries/schemas.mutations";
import { useCatalogs } from "@/queries/catalogs";

export function CreateSchemaDialog({
  ws,
  catalog,
  allowCatalogChoice = false,
  open,
  onOpenChange,
}: {
  ws: string;
  // A fixed catalog (opened from a catalog node). When omitted with
  // `allowCatalogChoice`, the dialog shows a catalog picker.
  catalog?: string;
  allowCatalogChoice?: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);

  const { data: catalogs = [] } = useCatalogs(ws);
  const defaultSlug =
    catalogs.find((c) => c.is_default)?.slug ?? catalogs[0]?.slug;
  // Fixed catalog wins; otherwise the user's pick, defaulting to the default.
  const effectiveCatalog = catalog ?? picked ?? defaultSlug;
  const create = useCreateSchema(ws, effectiveCatalog);

  function reset() {
    setName("");
    setPicked(undefined);
    setError(null);
  }

  async function handleCreate() {
    if (!name.trim()) {
      setError("Name is required");
      return;
    }
    if (allowCatalogChoice && !effectiveCatalog) {
      setError("Pick a catalog");
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
            {allowCatalogChoice
              ? "Create a new schema (namespace) in one of this workspace's catalogs."
              : "Create a new schema (namespace) in this catalog."}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-2">
          {allowCatalogChoice && (
            <div className="space-y-1.5">
              <Label htmlFor="schema-catalog">Catalog</Label>
              <Select value={effectiveCatalog ?? ""} onValueChange={setPicked}>
                <SelectTrigger id="schema-catalog" aria-label="catalog">
                  <SelectValue placeholder="Select a catalog…" />
                </SelectTrigger>
                <SelectContent>
                  {catalogs.map((c) => (
                    <SelectItem key={c.id} value={c.slug}>
                      {c.slug}
                      {c.is_default ? " (default)" : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
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
