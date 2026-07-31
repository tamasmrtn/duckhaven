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
import type { AccessMode } from "@/types/grant";
import { cn } from "@/utils";

// External, operator-owned stores. Bundled object storage (MinIO) is offered as
// an explicit choice and maps to omitting the backend (auto-provisioned).
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

const NAME_RE = /^[a-z][a-z0-9_]*$/;
const BUNDLED = "__bundled";
const NEW_BACKEND = "__new";

export function CreateCatalogDialog({
  ws,
  open,
  onOpenChange,
}: {
  ws: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Storage is a first-class choice on every catalog (not hidden behind an
  // "Advanced" toggle): bundled object storage, an existing backend, or a new
  // external one.
  const { data: backends = [] } = useStorageBackends();
  const createBackend = useCreateStorageBackend();
  const [backendChoice, setBackendChoice] = useState<string>(BUNDLED);
  const [kind, setKind] = useState<ExternalKind>("s3");
  const [backendName, setBackendName] = useState("");
  const [rootUri, setRootUri] = useState("");
  const [accessMode, setAccessMode] = useState<AccessMode>("open");

  const create = useCreateCatalog(ws);
  const pending = create.isPending || createBackend.isPending;

  function reset() {
    setName("");
    setError(null);
    setBackendChoice(BUNDLED);
    setKind("s3");
    setBackendName("");
    setRootUri("");
    setAccessMode("open");
  }

  async function handleCreate() {
    if (!NAME_RE.test(name.trim())) {
      setError(
        "Name must be lowercase letters, digits, or underscores and start with a letter.",
      );
      return;
    }
    try {
      // Bundled → omit the backend (API auto-provisions object storage); a new
      // external backend is registered first; otherwise use the chosen one.
      let storage_backend_id: string | undefined;
      if (backendChoice === NEW_BACKEND) {
        const sb = await createBackend.mutateAsync({
          kind,
          name: backendName,
          root_uri: rootUri,
        });
        storage_backend_id = sb.id;
      } else if (backendChoice !== BUNDLED) {
        storage_backend_id = backendChoice;
      }
      await create.mutateAsync({
        name: name.trim(),
        storage_backend_id,
        access_mode: accessMode,
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
            <Label htmlFor="catalog-name">Name</Label>
            <Input
              id="catalog-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="curated"
              autoFocus
            />
            <p className="text-2xs text-text-tertiary">
              Lowercase letters, digits, underscores; used in
              catalog.schema.table.
            </p>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="catalog-backend">Storage backend</Label>
            <select
              id="catalog-backend"
              value={backendChoice}
              onChange={(e) => setBackendChoice(e.target.value)}
              className="h-9 w-full rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 text-sm"
            >
              <option value={BUNDLED}>Bundled object storage (MinIO)</option>
              {backends
                .filter((b) => b.kind !== "object_store")
                .map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name} ({KIND_LABELS[b.kind as ExternalKind]})
                  </option>
                ))}
              <option value={NEW_BACKEND}>+ New external backend…</option>
            </select>

            {backendChoice === NEW_BACKEND && (
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

          {/* Set here rather than only on the permissions panel: a catalog
              created open is readable by every workspace member from the moment
              it exists, so narrowing it afterwards leaves a window. */}
          <div className="space-y-1.5">
            <Label htmlFor="catalog-access">Who can see its data</Label>
            <select
              id="catalog-access"
              value={accessMode}
              onChange={(e) => setAccessMode(e.target.value as AccessMode)}
              className="h-9 w-full rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] px-2 text-sm"
            >
              <option value="open">Everyone in this workspace</option>
              <option value="scoped">Only what I grant (scoped)</option>
            </select>
            <p className="text-2xs text-text-tertiary">
              {accessMode === "scoped"
                ? "Members see only the schemas and tables they are granted. You get full access to this catalog so you can grant the rest."
                : "Every workspace member gets their workspace role over everything in this catalog."}
            </p>
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
