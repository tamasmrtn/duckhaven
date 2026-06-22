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
import {
  useAllCatalogs,
  useAttachCatalog,
  useCreateCatalog,
} from "@/queries/catalogs";

export function CreateCatalogDialog({
  ws,
  open,
  onOpenChange,
}: {
  ws: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = useCreateCatalog(ws);

  function reset() {
    setSlug("");
    setName("");
    setError(null);
  }

  async function handleCreate() {
    if (!slug.trim() || !name.trim()) {
      setError("Slug and name are required");
      return;
    }
    try {
      await create.mutateAsync({ slug: slug.trim(), name: name.trim() });
      reset();
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create catalog");
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
          <DialogTitle>New catalog</DialogTitle>
          <DialogDescription>
            Create a catalog (its own Polaris catalog + storage) and attach it
            to this workspace.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-2">
          <div className="space-y-1.5">
            <Label htmlFor="catalog-slug">Slug</Label>
            <Input
              id="catalog-slug"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="curated"
              autoFocus
            />
            <p className="text-2xs text-text-tertiary">
              Lowercase letters, digits, underscores; used in
              catalog.schema.table.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="catalog-name">Name</Label>
            <Input
              id="catalog-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Curated"
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

export function AttachCatalogDialog({
  ws,
  attachedSlugs,
  open,
  onOpenChange,
}: {
  ws: string;
  attachedSlugs: string[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [catalogId, setCatalogId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { data: all } = useAllCatalogs();
  const attach = useAttachCatalog(ws);

  // Only offer catalogs not already attached to this workspace.
  const attachable = (all ?? []).filter((c) => !attachedSlugs.includes(c.slug));

  async function handleAttach() {
    if (!catalogId) {
      setError("Pick a catalog to attach");
      return;
    }
    try {
      await attach.mutateAsync({ catalogId });
      setCatalogId("");
      setError(null);
      onOpenChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to attach catalog");
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle>Attach catalog</DialogTitle>
          <DialogDescription>
            Attach an existing catalog to this workspace. The same catalog can
            be shared across workspaces.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-2">
          {attachable.length === 0 ? (
            <p className="text-sm text-text-tertiary">
              No other catalogs available to attach.
            </p>
          ) : (
            <Select value={catalogId} onValueChange={setCatalogId}>
              <SelectTrigger aria-label="catalog">
                <SelectValue placeholder="Select a catalog…" />
              </SelectTrigger>
              <SelectContent>
                {attachable.map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.name} ({c.slug})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleAttach}
            disabled={attach.isPending || attachable.length === 0}
          >
            {attach.isPending ? "Attaching…" : "Attach"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
