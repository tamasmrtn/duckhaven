import { useState } from "react";
import { Bot, Copy, KeyRound, MoreHorizontal, RefreshCw } from "lucide-react";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useCreateServiceAccount,
  useDeleteServiceAccount,
  useIssuePat,
  useRevokePat,
  useServiceAccountPats,
  useServiceAccounts,
  useUpdateServiceAccount,
} from "@/queries/service-accounts";
import type { PatToken, ServiceAccount } from "@/types/service-account";
import { ManageWorkspacesDialog } from "./UsersPage";

const ROLES = ["admin", "user"];
// Labels map to the API's `expires_in_days` (null = never).
const EXPIRY_OPTIONS: { value: string; label: string; days: number | null }[] =
  [
    { value: "30", label: "30 days", days: 30 },
    { value: "90", label: "90 days", days: 90 },
    { value: "365", label: "1 year", days: 365 },
    { value: "never", label: "Never", days: null },
  ];

function fmtDate(iso: string | null): string {
  return iso ? new Date(iso).toLocaleDateString() : "Never";
}

function CreateServiceAccountDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [name, setName] = useState("");
  const [role, setRole] = useState("user");
  const [error, setError] = useState<string | null>(null);
  const create = useCreateServiceAccount();

  async function handleCreate() {
    setError(null);
    try {
      await create.mutateAsync({ name, role });
      setName("");
      setRole("user");
      onOpenChange(false);
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Could not create service account.",
      );
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New service account</DialogTitle>
          <DialogDescription>
            A non-human principal that authenticates with a personal access
            token instead of a password. Grant it a role and workspace access
            like any user.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="new-sa-name">Name</Label>
            <Input
              id="new-sa-name"
              placeholder="e.g. ci-runner"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Role</Label>
            <Select value={role} onValueChange={setRole}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLES.map((r) => (
                  <SelectItem key={r} value={r}>
                    {r}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {error && (
            <p className="text-xs text-[var(--status-failed)]" role="alert">
              {error}
            </p>
          )}
        </div>
        <DialogFooter>
          <Button onClick={handleCreate} disabled={create.isPending || !name}>
            {create.isPending ? "Creating…" : "Create service account"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function PatModal({
  account,
  open,
  onOpenChange,
}: {
  account: ServiceAccount;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: pats = [], isLoading } = useServiceAccountPats(
    account.id,
    open,
  );
  const issue = useIssuePat(account.id);
  const revoke = useRevokePat(account.id);
  const [expiry, setExpiry] = useState("90");
  const [issued, setIssued] = useState<PatToken | null>(null);
  const [copied, setCopied] = useState(false);

  function handleIssue() {
    const days = EXPIRY_OPTIONS.find((o) => o.value === expiry)?.days ?? 90;
    issue.mutate({ expires_in_days: days }, { onSuccess: setIssued });
  }

  function handleCopy() {
    if (!issued) return;
    void navigator.clipboard.writeText(issued.token);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function handleClose() {
    setIssued(null);
    setCopied(false);
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Access tokens — {account.name}</DialogTitle>
          <DialogDescription>
            Issue a personal access token for this service account. Present it
            as
            <code className="mx-1 rounded bg-[var(--bg-code)] px-1 py-0.5 font-mono text-xs text-[var(--text-code)]">
              Authorization: Bearer
            </code>
            when calling the API.
          </DialogDescription>
        </DialogHeader>

        {issued ? (
          <div className="space-y-3 py-1">
            <p className="text-xs font-medium text-[var(--status-running)]">
              This is the only time this token will be shown.
            </p>
            <div className="relative">
              <pre
                className="overflow-x-auto rounded-md border border-[var(--border-subtle)] bg-[var(--bg-code)] p-3 font-mono text-xs text-[var(--text-code)]"
                data-testid="pat-secret"
              >
                {issued.token}
              </pre>
              <Button
                variant="ghost"
                size="icon"
                className="absolute right-2 top-2 size-7"
                onClick={handleCopy}
                aria-label="Copy token"
              >
                {copied ? (
                  <RefreshCw className="size-3.5 text-[var(--status-success)]" />
                ) : (
                  <Copy className="size-3.5" />
                )}
              </Button>
            </div>
            <p className="text-2xs text-text-tertiary">
              Expires: {fmtDate(issued.expires_at)}
            </p>
            <Button variant="outline" className="w-full" onClick={handleClose}>
              Done
            </Button>
          </div>
        ) : (
          <div className="space-y-4 py-1">
            <div className="flex items-end gap-2">
              <div className="flex-1 space-y-1.5">
                <Label>Expires</Label>
                <Select value={expiry} onValueChange={setExpiry}>
                  <SelectTrigger aria-label="Token expiry">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {EXPIRY_OPTIONS.map((o) => (
                      <SelectItem key={o.value} value={o.value}>
                        {o.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button onClick={handleIssue} disabled={issue.isPending}>
                {issue.isPending ? "Issuing…" : "Issue token"}
              </Button>
            </div>

            <Separator />

            <div className="space-y-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
                Active tokens
              </p>
              {isLoading ? (
                <Skeleton className="h-10 w-full animate-shimmer rounded-md" />
              ) : pats.length === 0 ? (
                <p className="py-4 text-center text-sm text-text-secondary">
                  No tokens yet.
                </p>
              ) : (
                pats.map((pat) => (
                  <div
                    key={pat.id}
                    className="flex items-center justify-between gap-3 rounded-md border border-[var(--border-subtle)] p-2"
                  >
                    <div className="min-w-0 text-xs">
                      <p className="font-mono">{pat.id}</p>
                      <p className="text-text-secondary">
                        Created {fmtDate(pat.created_at)} · Expires{" "}
                        {fmtDate(pat.expires_at)}
                      </p>
                    </div>
                    <Button
                      variant="destructive"
                      size="sm"
                      className="text-xs"
                      onClick={() => revoke.mutate(pat.id)}
                      disabled={revoke.isPending}
                    >
                      Revoke
                    </Button>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function ServiceAccountRow({
  account,
  onError,
}: {
  account: ServiceAccount;
  onError: (message: string) => void;
}) {
  const update = useUpdateServiceAccount();
  const remove = useDeleteServiceAccount();
  const [patsOpen, setPatsOpen] = useState(false);
  const [workspacesOpen, setWorkspacesOpen] = useState(false);

  async function handleDelete() {
    try {
      await remove.mutateAsync(account.id);
    } catch (e) {
      onError(
        e instanceof Error ? e.message : "Could not delete service account.",
      );
    }
  }

  return (
    <div className="flex items-center gap-3 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-3">
      <Avatar className="size-8">
        <AvatarFallback className="bg-accent">
          <Bot className="size-4" />
        </AvatarFallback>
      </Avatar>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium">{account.name}</p>
        <p className="text-xs text-text-secondary truncate">{account.email}</p>
      </div>
      {!account.is_active && (
        <Badge variant="secondary" className="text-2xs">
          Disabled
        </Badge>
      )}
      <Badge variant="outline" className="gap-1 text-2xs">
        <KeyRound className="size-3" />
        {account.pat_count}
      </Badge>
      <Select
        value={account.role}
        onValueChange={(role) =>
          update.mutate({ id: account.id, input: { role } })
        }
      >
        <SelectTrigger
          className="h-8 w-28"
          aria-label={`Role for ${account.name}`}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {ROLES.map((r) => (
            <SelectItem key={r} value={r}>
              {r}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            aria-label={`Actions for ${account.name}`}
          >
            <MoreHorizontal className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onClick={() => setPatsOpen(true)}>
            Manage tokens
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => setWorkspacesOpen(true)}>
            Manage workspaces
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={() =>
              update.mutate({
                id: account.id,
                input: { is_active: !account.is_active },
              })
            }
          >
            {account.is_active ? "Deactivate" : "Activate"}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={handleDelete}>Delete</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <PatModal account={account} open={patsOpen} onOpenChange={setPatsOpen} />
      <ManageWorkspacesDialog
        userId={account.id}
        userName={account.name}
        open={workspacesOpen}
        onOpenChange={setWorkspacesOpen}
      />
    </div>
  );
}

export function ServiceAccountsPage() {
  const { data: accounts = [], isLoading } = useServiceAccounts();
  const [createOpen, setCreateOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-6 py-3 shrink-0">
        <p className="text-xs text-text-secondary">
          {accounts.length} service accounts
        </p>
        <Button size="sm" className="h-8" onClick={() => setCreateOpen(true)}>
          <Bot className="size-4" />
          New service account
        </Button>
      </div>

      <CreateServiceAccountDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
      />

      <div className="flex-1 overflow-auto p-4">
        {error && (
          <p className="mb-3 text-xs text-[var(--status-failed)]" role="alert">
            {error}
          </p>
        )}
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton
                key={i}
                className="h-14 w-full animate-shimmer rounded-md"
              />
            ))}
          </div>
        ) : accounts.length === 0 ? (
          <EmptyState icon={Bot} title="No service accounts yet." />
        ) : (
          <div className="space-y-2">
            {accounts.map((a) => (
              <ServiceAccountRow key={a.id} account={a} onError={setError} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
