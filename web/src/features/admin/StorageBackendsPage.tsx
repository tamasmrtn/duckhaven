import { useState } from "react";
import { Plus, Trash2, ShieldAlert } from "lucide-react";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useStorageBackends,
  useCreateStorageBackend,
  useDeleteStorageBackend,
} from "@/queries/storage-backends";
import { StorageIcon } from "@/components/app/StorageIcon";
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

type WizardStep = 1 | 2 | 3;

function RegisterWizard({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [step, setStep] = useState<WizardStep>(1);
  const [kind, setKind] = useState<BackendKind>("s3");
  const [name, setName] = useState("");
  const [uri, setUri] = useState("");
  const [credId, setCredId] = useState("");
  const create = useCreateStorageBackend();

  function reset() {
    setStep(1);
    setKind("s3");
    setName("");
    setUri("");
    setCredId("");
  }

  async function handleFinish() {
    await create.mutateAsync({
      kind,
      name,
      root_uri: uri,
      uc_storage_credential_id: credId || undefined,
    });
    reset();
    onClose();
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) {
          reset();
          onClose();
        }
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Register storage backend — Step {step} of 3</DialogTitle>
        </DialogHeader>

        {step === 1 && (
          <div className="space-y-4 py-2">
            <p className="text-sm text-text-secondary">
              Choose the backend kind.
            </p>
            <div className="grid grid-cols-2 gap-2">
              {(Object.keys(KIND_LABELS) as BackendKind[]).map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => setKind(k)}
                  className={cn(
                    "flex items-center gap-2 rounded-md border p-3 text-sm transition-colors",
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
            <DialogFooter>
              <Button onClick={() => setStep(2)}>Next</Button>
            </DialogFooter>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <Label className="text-sm">Name</Label>
              <Input
                placeholder="acme-prod"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-sm">Root URI</Label>
              <Input
                placeholder={KIND_URI_PLACEHOLDER[kind]}
                value={uri}
                onChange={(e) => setUri(e.target.value)}
                className="font-mono text-xs"
              />
            </div>
            <DialogFooter className="gap-2">
              <Button variant="outline" onClick={() => setStep(1)}>
                Back
              </Button>
              <Button onClick={() => setStep(3)} disabled={!name || !uri}>
                Next
              </Button>
            </DialogFooter>
          </div>
        )}

        {step === 3 && (
          <div className="space-y-4 py-2">
            {kind === "s3" || kind === "adls_gen2" ? (
              <>
                <p className="text-sm text-text-secondary">
                  Bind a Unity Catalog storage credential for short-lived
                  credential vending.
                </p>
                <div className="space-y-1.5">
                  <Label className="text-sm">UC Storage Credential ID</Label>
                  <Input
                    placeholder="uc-cred-id"
                    value={credId}
                    onChange={(e) => setCredId(e.target.value)}
                    className="font-mono text-xs"
                  />
                </div>
              </>
            ) : (
              <p className="text-sm text-text-secondary">
                No UC credential needed for {KIND_LABELS[kind]} backends —
                access is via filesystem permissions.
              </p>
            )}
            <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-3 text-xs space-y-1">
              <p className="font-medium text-text-primary">Summary</p>
              <p>
                <span className="text-text-secondary">Kind:</span>{" "}
                {KIND_LABELS[kind]}
              </p>
              <p>
                <span className="text-text-secondary">Name:</span> {name}
              </p>
              <p className="font-mono break-all">
                <span className="text-text-secondary">URI:</span> {uri}
              </p>
            </div>
            <DialogFooter className="gap-2">
              <Button variant="outline" onClick={() => setStep(2)}>
                Back
              </Button>
              <Button onClick={handleFinish} disabled={create.isPending}>
                {create.isPending ? "Registering…" : "Register backend"}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function StorageBackendsPage() {
  const { data: backends = [], isLoading } = useStorageBackends();
  const deleteBackend = useDeleteStorageBackend();
  const [wizardOpen, setWizardOpen] = useState(false);

  const hasLocalBackend = backends.some(
    (b) => b.kind === "local_fs" || b.kind === "nas",
  );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-6 py-3 shrink-0">
        <p className="text-xs text-text-secondary font-tabular">
          {backends.length} backends
        </p>
        <Button
          size="sm"
          className="h-8 gap-1.5 text-xs"
          onClick={() => setWizardOpen(true)}
        >
          <Plus className="size-3.5" />
          Register backend
        </Button>
      </div>

      {hasLocalBackend && (
        <Banner className="mx-6 mt-3">
          <ShieldAlert className="size-3.5 text-[var(--brand-orange)]" />
          <span>
            Local FS / NAS backends have no off-box disaster recovery — data DR
            is delegated to that disk. Ensure off-box backups (see the runbook).
          </span>
        </Banner>
      )}

      <div className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="space-y-1 p-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton
                key={i}
                className="h-10 w-full animate-shimmer rounded"
              />
            ))}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-[var(--bg-surface)] z-10">
              <tr className="border-b border-[var(--border-subtle)]">
                {["Kind", "Name", "Root URI", "UC cred", "In use", ""].map(
                  (h) => (
                    <th
                      key={h}
                      className="px-4 py-2 text-left text-xs font-medium text-text-secondary"
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {backends.map((b, i) => (
                <tr
                  key={b.id}
                  className={cn(
                    "border-b border-[var(--border-subtle)] hover:bg-accent/50",
                    i % 2 === 0 ? "" : "bg-[var(--bg-surface)]/40",
                  )}
                >
                  <td className="px-4 py-2">
                    <StorageIcon
                      kind={b.kind}
                      className="text-text-secondary"
                    />
                  </td>
                  <td className="px-4 py-2 font-medium">{b.name}</td>
                  <td className="px-4 py-2 font-mono text-2xs text-text-secondary max-w-[260px] truncate">
                    {b.root_uri}
                  </td>
                  <td className="px-4 py-2">
                    {b.uc_storage_credential_id ? (
                      <span
                        className={cn(
                          "text-xs",
                          b.uc_credential_valid
                            ? "text-[var(--status-success)]"
                            : "text-[var(--status-failed)]",
                        )}
                      >
                        {b.uc_credential_valid ? "✓ valid" : "✗ invalid"}
                      </span>
                    ) : (
                      <span className="text-xs text-text-tertiary">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-text-secondary font-tabular">
                    {b.workspace_count} ws
                  </td>
                  <td className="px-4 py-2">
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <span>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-7"
                              disabled={b.workspace_count > 0}
                              onClick={() => deleteBackend.mutate(b.id)}
                              aria-label={`Delete ${b.name}`}
                            >
                              <Trash2 className="size-3.5 text-text-tertiary" />
                            </Button>
                          </span>
                        </TooltipTrigger>
                        {b.workspace_count > 0 && (
                          <TooltipContent>
                            In use by {b.workspace_count} workspace
                            {b.workspace_count !== 1 ? "s" : ""}
                          </TooltipContent>
                        )}
                      </Tooltip>
                    </TooltipProvider>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <RegisterWizard open={wizardOpen} onClose={() => setWizardOpen(false)} />
    </div>
  );
}
