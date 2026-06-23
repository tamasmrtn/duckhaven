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
import { StorageIcon } from "@/components/app/StorageIcon";
import {
  useAllCatalogs,
  useAttachCatalog,
  useCreateCatalog,
} from "@/queries/catalogs";
import {
  useCreateStorageBackend,
  useStorageBackends,
} from "@/queries/storage-backends";
import { cn } from "@/utils";

// External, operator-owned stores only. Bundled object storage (MinIO) needs no
// configuration — it is auto-provisioned when no backend is chosen.
const EXTERNAL_KINDS = ["s3", "adls_gen2"] as const;
type ExternalKind = (typeof EXTERNAL_KINDS)[number];
const KIND_LABELS: Record<ExternalKind, string> = {
  s3: "S3",
  adls_gen2: "ADLS Gen 2",
};
const KIND_URI_PLACEHOLDER: Record<ExternalKind, string> = {
  s3: "s3://my-bucket/duckhaven/",
  adls_gen2: "abfss://container@account.dfs.core.windows.net/duckhaven/",
};

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

  // Storage selection (moved here from workspace creation): default to bundled
  // object storage; advanced lets the user pick an existing or external backend.
  const { data: backends = [] } = useStorageBackends();
  const createBackend = useCreateStorageBackend();
  const [advanced, setAdvanced] = useState(false);
  const [pickNew, setPickNew] = useState(false);
  const [selectedBackendId, setSelectedBackendId] = useState("");
  const [kind, setKind] = useState<ExternalKind>("s3");
  const [backendName, setBackendName] = useState("");
  const [rootUri, setRootUri] = useState("");

  const create = useCreateCatalog(ws);
  const creatingBackend = pickNew || (advanced && backends.length === 0);
  const pending = create.isPending || createBackend.isPending;

  function reset() {
    setSlug("");
    setName("");
    setError(null);
    setAdvanced(false);
    setPickNew(false);
    setSelectedBackendId("");
    setKind("s3");
    setBackendName("");
    setRootUri("");
  }

  async function handleCreate() {
    if (!slug.trim() || !name.trim()) {
      setError("Slug and name are required");
      return;
    }
    try {
      // Default: omit the backend so the API auto-provisions bundled object
      // storage (MinIO). Advanced picks an existing or external backend.
      let storage_backend_id: string | undefined;
      if (advanced && creatingBackend) {
        const sb = await createBackend.mutateAsync({
          kind,
          name: backendName,
          root_uri: rootUri,
        });
        storage_backend_id = sb.id;
      } else if (advanced) {
        const sb =
          backends.find((b) => b.id === selectedBackendId) ?? backends[0];
        storage_backend_id = sb?.id;
      }
      await create.mutateAsync({
        slug: slug.trim(),
        name: name.trim(),
        storage_backend_id,
      });
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

          <div className="space-y-2">
            {!advanced && (
              <p className="text-2xs text-text-tertiary">
                Tables live in the bundled object storage (MinIO).
              </p>
            )}
            <button
              type="button"
              onClick={() => setAdvanced((v) => !v)}
              className="text-2xs text-text-tertiary underline-offset-2 hover:text-text-secondary hover:underline"
            >
              {advanced
                ? "Use bundled object storage"
                : "Advanced: use an existing or external store"}
            </button>

            {advanced && (
              <div className="space-y-1.5">
                <Label htmlFor="catalog-backend">Storage backend</Label>
                {backends.length > 0 && (
                  <select
                    id="catalog-backend"
                    value={creatingBackend ? "__new" : selectedBackendId}
                    onChange={(e) => {
                      if (e.target.value === "__new") {
                        setPickNew(true);
                      } else {
                        setPickNew(false);
                        setSelectedBackendId(e.target.value);
                      }
                    }}
                    className="h-9 w-full rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 text-sm"
                  >
                    <option value="" disabled>
                      Select a backend…
                    </option>
                    {backends.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.name} (
                        {b.kind === "object_store"
                          ? "Object storage"
                          : KIND_LABELS[b.kind as ExternalKind]}
                        )
                      </option>
                    ))}
                    <option value="__new">+ New external backend…</option>
                  </select>
                )}

                {creatingBackend && (
                  <div className="space-y-3 rounded-md border border-[var(--border-subtle)] p-3">
                    <div className="grid grid-cols-2 gap-2">
                      {EXTERNAL_KINDS.map((k) => (
                        <button
                          key={k}
                          type="button"
                          onClick={() => setKind(k)}
                          className={cn(
                            "flex items-center gap-2 rounded-md border p-2 text-sm transition-colors",
                            kind === k
                              ? "border-[var(--brand-slate-blue)] bg-accent text-text-primary"
                              : "border-[var(--border-subtle)] text-text-secondary hover:border-[var(--border-strong)]",
                          )}
                        >
                          <StorageIcon kind={k} />
                          {KIND_LABELS[k]}
                        </button>
                      ))}
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="catalog-sb-name">Backend name</Label>
                      <Input
                        id="catalog-sb-name"
                        value={backendName}
                        onChange={(e) => setBackendName(e.target.value)}
                        placeholder="primary-store"
                        className="h-9"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="catalog-sb-uri">Root URI</Label>
                      <Input
                        id="catalog-sb-uri"
                        value={rootUri}
                        onChange={(e) => setRootUri(e.target.value)}
                        placeholder={KIND_URI_PLACEHOLDER[kind]}
                        className="h-9 font-mono text-xs"
                      />
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {error && <p className="text-xs text-red-500">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleCreate} disabled={pending}>
            {pending ? "Creating…" : "Create"}
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
