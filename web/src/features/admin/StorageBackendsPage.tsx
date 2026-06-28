import { useState } from "react";
import { Plus, Trash2, ShieldAlert, ShieldCheck, ShieldX } from "lucide-react";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
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
  useCheckStorageBackendHealth,
  useDeleteStorageBackend,
} from "@/queries/storage-backends";
import { StorageIcon } from "@/components/app/StorageIcon";
import type {
  BackendKind,
  StorageBackendConfig,
  StorageBackendHealth,
} from "@/types/storage-backend";
import { cn, plural } from "@/utils";

const KIND_LABELS: Record<BackendKind, string> = {
  object_store: "Object storage (MinIO)",
  s3: "S3",
  adls_gen2: "ADLS Gen 2",
};

const KIND_URI_PLACEHOLDER: Record<BackendKind, string> = {
  object_store: "acme/ (optional prefix)",
  s3: "s3://my-bucket/duckhaven/",
  adls_gen2: "abfss://container@account.dfs.core.windows.net/duckhaven/",
};

type WizardStep = 1 | 2 | 3 | 4;

const EMPTY_CONFIG = {
  role_arn: "",
  region: "",
  external_id: "",
  endpoint: "",
  path_style_access: false,
  tenant_id: "",
  multi_tenant_app_name: "",
  consent_url: "",
  hierarchical: false,
};

function buildConfig(
  kind: BackendKind,
  c: typeof EMPTY_CONFIG,
): StorageBackendConfig | undefined {
  if (kind === "s3") {
    return {
      role_arn: c.role_arn.trim(),
      region: c.region.trim(),
      ...(c.external_id.trim() ? { external_id: c.external_id.trim() } : {}),
      ...(c.endpoint.trim() ? { endpoint: c.endpoint.trim() } : {}),
      ...(c.path_style_access ? { path_style_access: true } : {}),
    };
  }
  if (kind === "adls_gen2") {
    return {
      tenant_id: c.tenant_id.trim(),
      ...(c.multi_tenant_app_name.trim()
        ? { multi_tenant_app_name: c.multi_tenant_app_name.trim() }
        : {}),
      ...(c.consent_url.trim() ? { consent_url: c.consent_url.trim() } : {}),
      ...(c.hierarchical ? { hierarchical: true } : {}),
    };
  }
  return undefined;
}

function configComplete(kind: BackendKind, c: typeof EMPTY_CONFIG): boolean {
  if (kind === "s3") return !!c.role_arn.trim() && !!c.region.trim();
  if (kind === "adls_gen2") return !!c.tenant_id.trim();
  return true;
}

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
  const [config, setConfig] = useState({ ...EMPTY_CONFIG });
  const create = useCreateStorageBackend();

  function set<K extends keyof typeof EMPTY_CONFIG>(
    key: K,
    value: (typeof EMPTY_CONFIG)[K],
  ) {
    setConfig((prev) => ({ ...prev, [key]: value }));
  }

  function reset() {
    setStep(1);
    setKind("s3");
    setName("");
    setUri("");
    setConfig({ ...EMPTY_CONFIG });
    create.reset();
  }

  async function handleFinish() {
    await create.mutateAsync({
      kind,
      name,
      root_uri: uri,
      config: buildConfig(kind, config),
    });
    setStep(4);
  }

  const isExternal = kind === "s3" || kind === "adls_gen2";

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
          <DialogTitle>
            {step === 4
              ? "Backend registered"
              : `Register storage backend — Step ${step} of 3`}
          </DialogTitle>
          <DialogDescription>
            Configure a storage location (object storage, S3, or ADLS) that
            workspaces can use for their tables.
          </DialogDescription>
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
              <Label className="text-sm">
                Root URI
                {kind === "object_store" && (
                  <span className="ml-1 text-text-tertiary">(optional)</span>
                )}
              </Label>
              <Input
                placeholder={KIND_URI_PLACEHOLDER[kind]}
                value={uri}
                onChange={(e) => setUri(e.target.value)}
                className="font-mono text-xs"
              />
              {kind === "object_store" && (
                <p className="text-2xs text-text-tertiary">
                  Leave blank to use the bundled MinIO bucket root. A value is a
                  prefix label within that bucket.
                </p>
              )}
            </div>
            <DialogFooter className="gap-2">
              <Button variant="outline" onClick={() => setStep(1)}>
                Back
              </Button>
              <Button
                onClick={() => setStep(3)}
                disabled={!name || (kind !== "object_store" && !uri)}
              >
                Next
              </Button>
            </DialogFooter>
          </div>
        )}

        {step === 3 && (
          <div className="space-y-4 py-2">
            {kind === "s3" && (
              <div className="space-y-3">
                <p className="text-sm text-text-secondary">
                  Polaris assumes this IAM role via STS to vend short-lived
                  scoped credentials. No access keys are stored.
                </p>
                <Field label="Role ARN" required>
                  <Input
                    placeholder="arn:aws:iam::123456789012:role/duckhaven"
                    value={config.role_arn}
                    onChange={(e) => set("role_arn", e.target.value)}
                    className="font-mono text-xs"
                    autoFocus
                  />
                </Field>
                <Field label="Region" required>
                  <Input
                    placeholder="us-east-1"
                    value={config.region}
                    onChange={(e) => set("region", e.target.value)}
                    className="font-mono text-xs"
                  />
                </Field>
                <Field label="External ID">
                  <Input
                    placeholder="dh-acme (confused-deputy guard)"
                    value={config.external_id}
                    onChange={(e) => set("external_id", e.target.value)}
                    className="font-mono text-xs"
                  />
                </Field>
                <Field label="Endpoint (S3-compatible only)">
                  <Input
                    placeholder="leave blank for AWS S3"
                    value={config.endpoint}
                    onChange={(e) => set("endpoint", e.target.value)}
                    className="font-mono text-xs"
                  />
                </Field>
                <Checkbox
                  label="Path-style access"
                  checked={config.path_style_access}
                  onChange={(v) => set("path_style_access", v)}
                />
              </div>
            )}

            {kind === "adls_gen2" && (
              <div className="space-y-3">
                <p className="text-sm text-text-secondary">
                  Polaris vends a scoped SAS token through the consented Entra
                  app. No account key is stored.
                </p>
                <Field label="Tenant ID" required>
                  <Input
                    placeholder="00000000-0000-0000-0000-000000000000"
                    value={config.tenant_id}
                    onChange={(e) => set("tenant_id", e.target.value)}
                    className="font-mono text-xs"
                    autoFocus
                  />
                </Field>
                <Field label="Multi-tenant app name">
                  <Input
                    placeholder="polaris-storage-app"
                    value={config.multi_tenant_app_name}
                    onChange={(e) =>
                      set("multi_tenant_app_name", e.target.value)
                    }
                    className="font-mono text-xs"
                  />
                </Field>
                <Field label="Consent URL">
                  <Input
                    placeholder="https://login.microsoftonline.com/…"
                    value={config.consent_url}
                    onChange={(e) => set("consent_url", e.target.value)}
                    className="font-mono text-xs"
                  />
                </Field>
                <Checkbox
                  label="Hierarchical namespace (ADLS Gen2 HNS)"
                  checked={config.hierarchical}
                  onChange={(v) => set("hierarchical", v)}
                />
              </div>
            )}

            {kind === "object_store" && (
              <p className="text-sm text-text-secondary">
                No credential needed — object storage is the bundled MinIO
                object store, which Polaris accesses with the stack&apos;s
                configured credentials. The Root URI is a prefix label within
                that bucket.
              </p>
            )}

            {create.isError && (
              <p className="text-xs text-[var(--status-failed)]">
                Registration failed. Check the config and try again.
              </p>
            )}

            <DialogFooter className="gap-2">
              <Button variant="outline" onClick={() => setStep(2)}>
                Back
              </Button>
              <Button
                onClick={handleFinish}
                disabled={create.isPending || !configComplete(kind, config)}
              >
                {create.isPending ? "Registering…" : "Register backend"}
              </Button>
            </DialogFooter>
          </div>
        )}

        {step === 4 && (
          <div className="space-y-4 py-2">
            <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-3 text-sm space-y-2">
              <p className="font-medium text-text-primary">
                {KIND_LABELS[kind]} backend “{name}” is registered.
              </p>
              {isExternal ? (
                <p className="text-text-secondary text-xs">
                  {kind === "s3"
                    ? "Make sure the IAM role’s trust policy lets the Polaris principal assume it (with the external id, if set), then run Test access to confirm credential vending reaches the bucket."
                    : "Grant admin consent to the Entra app and assign it the Storage Blob Data Contributor role on the account, then run Test access to confirm credential vending reaches the container."}
                </p>
              ) : (
                <p className="text-text-secondary text-xs">
                  Ready to use — workspaces can create catalogs on it.
                </p>
              )}
            </div>
            <DialogFooter>
              <Button
                onClick={() => {
                  reset();
                  onClose();
                }}
              >
                Done
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-sm">
        {label}
        {required && (
          <span className="ml-1 text-[var(--status-failed)]">*</span>
        )}
      </Label>
      {children}
    </div>
  );
}

function Checkbox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-text-secondary">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="size-3.5 accent-[var(--brand-slate-blue)]"
      />
      {label}
    </label>
  );
}

function HealthCell({ id, kind }: { id: string; kind: BackendKind }) {
  const check = useCheckStorageBackendHealth();
  const [result, setResult] = useState<StorageBackendHealth | null>(null);

  if (kind === "object_store") {
    return <span className="text-xs text-text-tertiary">—</span>;
  }

  async function run() {
    try {
      setResult(await check.mutateAsync(id));
    } catch {
      setResult({ valid: false, detail: "Health check request failed." });
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        className="h-6 text-2xs"
        onClick={run}
        disabled={check.isPending}
      >
        {check.isPending ? "Testing…" : "Test access"}
      </Button>
      {result && (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span>
                {result.valid ? (
                  <ShieldCheck className="size-4 text-[var(--status-success)]" />
                ) : (
                  <ShieldX className="size-4 text-[var(--status-failed)]" />
                )}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              {result.detail}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )}
    </div>
  );
}

export function StorageBackendsPage() {
  const { data: backends = [], isLoading } = useStorageBackends();
  const deleteBackend = useDeleteStorageBackend();
  const [wizardOpen, setWizardOpen] = useState(false);

  const hasLocalBackend = backends.some((b) => b.kind === "object_store");

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-6 py-3 shrink-0">
        <p className="text-xs text-text-secondary font-tabular">
          {plural(backends.length, "backend")}
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
            Object storage backends are stored in the bundled MinIO object store
            on the control-plane host — no off-box disaster recovery by default.
            Ensure off-box backups (see the runbook).
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
                {["Kind", "Name", "Root URI", "Access", "In use", ""].map(
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
                    <HealthCell id={b.id} kind={b.kind} />
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
