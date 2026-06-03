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
import { StorageIcon } from "@/components/app/StorageIcon";
import {
  useStorageBackends,
  useCreateStorageBackend,
} from "@/queries/storage-backends";
import { useCreateWorkspace } from "@/queries/workspaces";
import type { BackendKind } from "@/types/storage-backend";
import { cn } from "@/utils";

const KIND_LABELS: Record<BackendKind, string> = {
  local_fs: "Local FS",
  nas: "NAS",
  s3: "S3",
  adls_gen2: "ADLS Gen 2",
};

const KIND_URI_PLACEHOLDER: Record<BackendKind, string> = {
  local_fs: "file:///var/duckhaven/data/",
  nas: "file:///mnt/nas01/",
  s3: "s3://my-bucket/duckhaven/",
  adls_gen2: "abfss://container@account.dfs.core.windows.net/duckhaven/",
};

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
  const { data: backends = [] } = useStorageBackends();
  const createBackend = useCreateStorageBackend();
  const createWorkspace = useCreateWorkspace();

  const [wsName, setWsName] = useState("");
  const [pickNew, setPickNew] = useState(false);
  const [selectedBackendId, setSelectedBackendId] = useState("");
  const [kind, setKind] = useState<BackendKind>("local_fs");
  const [backendName, setBackendName] = useState("");
  const [rootUri, setRootUri] = useState("");
  const [error, setError] = useState("");

  const slug = slugify(wsName);
  // A fresh deployment has no backends, so creating one is the only option.
  const creatingBackend = pickNew || backends.length === 0;
  const pending = createBackend.isPending || createWorkspace.isPending;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      let backendId: string;
      if (creatingBackend) {
        const sb = await createBackend.mutateAsync({
          kind,
          name: backendName,
          root_uri: rootUri,
        });
        backendId = sb.id;
      } else {
        const sb =
          backends.find((b) => b.id === selectedBackendId) ?? backends[0];
        backendId = sb.id;
      }
      const ws = await createWorkspace.mutateAsync({
        slug,
        name: wsName,
        storage_backend_id: backendId,
      });
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
            Name the workspace and choose (or create) the storage backend its
            tables live on.
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

          <div className="space-y-1.5">
            <Label htmlFor="ws-backend">Storage backend</Label>
            {backends.length > 0 && (
              <select
                id="ws-backend"
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
                    {b.name} ({KIND_LABELS[b.kind]})
                  </option>
                ))}
                <option value="__new">+ New backend…</option>
              </select>
            )}

            {creatingBackend && (
              <div className="space-y-3 rounded-md border border-[var(--border-subtle)] p-3">
                <div className="grid grid-cols-2 gap-2">
                  {(Object.keys(KIND_LABELS) as BackendKind[]).map((k) => (
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
                  <Label htmlFor="sb-name">Backend name</Label>
                  <Input
                    id="sb-name"
                    value={backendName}
                    onChange={(e) => setBackendName(e.target.value)}
                    placeholder="primary-store"
                    required
                    className="h-9"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="sb-uri">Root URI</Label>
                  <Input
                    id="sb-uri"
                    value={rootUri}
                    onChange={(e) => setRootUri(e.target.value)}
                    placeholder={KIND_URI_PLACEHOLDER[kind]}
                    required
                    className="h-9 font-mono text-xs"
                  />
                </div>
              </div>
            )}
          </div>

          {error && (
            <p className="text-xs text-[var(--status-failed)]" role="alert">
              {error}
            </p>
          )}

          <DialogFooter>
            <Button type="submit" disabled={pending || !slug}>
              {pending ? "Creating…" : "Create workspace"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
