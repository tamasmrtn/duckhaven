import { useState } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import {
  CheckCircle2,
  Circle,
  AlertCircle,
  Copy,
  Cpu,
  Loader2,
  Power,
  RefreshCw,
  RotateCw,
  Server,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/app/EmptyState";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useAdminAgents,
  useBootstrapAgent,
  useComputeOptions,
  useCreateElasticAgent,
  useDeleteAgent,
  useRestartAgent,
  useTerminateAgent,
  useRevokeAgent,
} from "@/queries/agents";
import type { Agent, AgentStatus, BootstrapToken } from "@/types/agent";
import { cn, plural } from "@/utils";

// The currency comes from the configured provider (its own pricing page quotes
// the rates an operator copies). It is deliberately required rather than defaulted:
// a provider that prices nothing returns null, and the caller must then render no
// cost at all instead of putting a cloud symbol on hardware you already own.
function formatCost(cost: number, currency: string): string {
  const symbol = currency === "USD" ? "$" : `${currency} `;
  return `${symbol}${cost.toFixed(2)}/hr`;
}

function buildComposeSnippet(token: BootstrapToken, name?: string): string {
  return [
    "services:",
    "  duckhaven-agent:",
    `    image: ${token.agent_image}`,
    "    restart: unless-stopped",
    "    environment:",
    `      CONTROL_PLANE_URL: ${token.control_plane_url}`,
    `      BOOTSTRAP_TOKEN: ${token.token}`,
    ...(name ? [`      AGENT_NAME: ${name}`] : []),
    "      # Bind the agent's result server to all interfaces so the control",
    "      # plane can fetch query results back across the host boundary.",
    "      RESULTS_HTTP_HOST: 0.0.0.0",
    "    volumes:",
    "      - agent_results:/var/duckhaven-agent/results",
    "",
    "volumes:",
    "  agent_results:",
    "",
  ].join("\n");
}

const statusIcon: Record<AgentStatus, React.ReactNode> = {
  healthy: <CheckCircle2 className="size-4 text-[var(--status-success)]" />,
  degraded: <AlertCircle className="size-4 text-[var(--status-running)]" />,
  unavailable: <Circle className="size-4 text-[var(--status-failed)]" />,
};

const statusDot: Record<AgentStatus, string> = {
  healthy: "bg-[var(--status-success)]",
  degraded: "bg-[var(--status-running)]",
  unavailable: "bg-[var(--status-failed)]",
};

// A provisioning/terminating elastic agent isn't "down" — it's in transition, so
// it shows amber rather than the red of a genuinely unavailable agent.
function agentDotClass(agent: Agent): string {
  if (agent.lifecycle === "provisioning" || agent.lifecycle === "terminating") {
    return "bg-[var(--status-running)]";
  }
  return statusDot[agent.status];
}

function relativeTime(iso: string | null) {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 5) return "just now";
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

interface AgentDrawerProps {
  agent: Agent | null;
  open: boolean;
  onClose: () => void;
}

function AgentDrawer({ agent, open, onClose }: AgentDrawerProps) {
  // Cost is only renderable once the provider names a currency; see formatCost.
  const { data: computeOptions } = useComputeOptions();
  const currency = computeOptions?.currency ?? null;
  const revoke = useRevokeAgent();
  const restart = useRestartAgent();
  const terminate = useTerminateAgent();
  const deleteAgent = useDeleteAgent();
  const navigate = useNavigate();
  const { ws } = useParams({ from: "/$ws/admin/agents" });
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (!agent) return null;

  const restartable =
    !!agent.provider &&
    (agent.lifecycle === "terminated" || agent.lifecycle === "failed");
  const terminable =
    !!agent.provider &&
    (agent.lifecycle === "running" || agent.lifecycle === "provisioning");

  return (
    <>
      <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
        <SheetContent className="w-[480px] overflow-auto">
          <SheetHeader>
            <SheetTitle className="flex items-center gap-2">
              {statusIcon[agent.status]}
              {agent.name}
            </SheetTitle>
            <SheetDescription>
              Agent status, capabilities, and credential management.
            </SheetDescription>
          </SheetHeader>

          <div className="mt-6 space-y-4">
            {agent.provider && (
              <>
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary mb-2">
                    Elastic compute
                  </p>
                  <div className="space-y-1 text-sm">
                    <div className="flex justify-between">
                      <span className="text-text-secondary">Lifecycle</span>
                      <span className="font-mono text-xs">
                        {agent.lifecycle ?? "—"}
                      </span>
                    </div>
                    {agent.requested_cpu != null &&
                      agent.requested_memory_gb != null && (
                        <div className="flex justify-between">
                          <span className="text-text-secondary">Size</span>
                          <span className="font-mono font-tabular text-xs">
                            {agent.requested_cpu} vCPU ·{" "}
                            {agent.requested_memory_gb} GB
                          </span>
                        </div>
                      )}
                    {agent.hourly_cost != null && currency != null && (
                      <div className="flex justify-between">
                        <span className="text-text-secondary">Cost</span>
                        <span className="font-mono font-tabular text-xs font-medium">
                          {formatCost(agent.hourly_cost, currency)}
                        </span>
                      </div>
                    )}
                    <div className="flex justify-between">
                      <span className="text-text-secondary">Idle timeout</span>
                      <span className="font-mono font-tabular text-xs">
                        {agent.idle_timeout_minutes != null
                          ? `${agent.idle_timeout_minutes} min`
                          : "default"}
                      </span>
                    </div>
                  </div>
                </div>
                <Separator />
              </>
            )}

            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary mb-2">
                Capabilities
              </p>
              {agent.capabilities ? (
                <div className="space-y-1 text-sm">
                  <div className="flex justify-between">
                    <span className="text-text-secondary">DuckDB</span>
                    <span className="font-mono">
                      {agent.capabilities.duckdb_version}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-secondary">Memory cap</span>
                    <span className="font-mono font-tabular">
                      {agent.capabilities.memory_limit_gb} GB
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-text-secondary">Cores</span>
                    <span className="font-mono font-tabular">
                      {agent.capabilities.cores}
                    </span>
                  </div>
                  {agent.capabilities.tailscale_ip && (
                    <div className="flex justify-between">
                      <span className="text-text-secondary">Tailscale IP</span>
                      <span className="font-mono text-xs">
                        {agent.capabilities.tailscale_ip}
                      </span>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-text-secondary">Extensions</span>
                    <span className="font-mono text-xs text-right max-w-[240px] truncate">
                      {agent.capabilities.extensions.join(", ")}
                    </span>
                  </div>
                </div>
              ) : (
                <p className="text-sm text-text-tertiary">
                  Not yet reported — the agent has not registered.
                </p>
              )}
            </div>

            <Separator />

            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary mb-2">
                Recent errors
              </p>
              <p className="text-sm text-text-tertiary">0</p>
            </div>

            <Separator />

            <div className="flex flex-wrap gap-2">
              {restartable && (
                <Button
                  size="sm"
                  className="gap-1.5 text-xs"
                  onClick={() => restart.mutate(agent.id)}
                  disabled={restart.isPending}
                >
                  <RotateCw className="size-3.5" />
                  {restart.isPending ? "Restarting…" : "Restart agent"}
                </Button>
              )}
              {terminable && (
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1.5 text-xs"
                  onClick={() => terminate.mutate(agent.id)}
                  disabled={terminate.isPending}
                >
                  <Power className="size-3.5" />
                  {terminate.isPending ? "Terminating…" : "Terminate"}
                </Button>
              )}
              {!agent.provider && (
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1.5 text-xs"
                  onClick={() => revoke.mutate(agent.id)}
                  disabled={revoke.isPending}
                >
                  Revoke credential
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5 text-xs"
                onClick={() => {
                  onClose();
                  navigate({
                    to: "/$ws/history",
                    params: { ws },
                    search: { agent: agent.id },
                  });
                }}
              >
                View audit for this agent
              </Button>
              <Button
                variant="destructive"
                size="sm"
                className="gap-1.5 text-xs"
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 className="size-3.5" />
                Delete
              </Button>
            </div>
          </div>
        </SheetContent>
      </Sheet>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete agent “{agent.name}”?</DialogTitle>
            <DialogDescription>
              This permanently removes the agent and cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <ul className="list-disc space-y-1 py-2 pl-5 text-sm text-text-secondary">
            {agent.provider &&
              (agent.lifecycle === "running" ||
                agent.lifecycle === "provisioning") && (
                <li>
                  Its running cloud instance is terminated immediately (billing
                  stops).
                </li>
              )}
            <li>
              It cannot be restarted afterwards — you would create a new agent.
            </li>
            <li>
              Past queries stay in history but lose their link to this agent.
            </li>
          </ul>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteAgent.isPending}
              onClick={() =>
                deleteAgent.mutate(agent.id, {
                  onSuccess: () => {
                    setConfirmDelete(false);
                    onClose();
                  },
                })
              }
            >
              {deleteAgent.isPending ? "Deleting…" : "Delete permanently"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

interface BootstrapModalProps {
  open: boolean;
  onClose: () => void;
}

function BootstrapModal({ open, onClose }: BootstrapModalProps) {
  const bootstrap = useBootstrapAgent();
  const [token, setToken] = useState<BootstrapToken | null>(null);
  const [name, setName] = useState("");
  const [copied, setCopied] = useState(false);

  function handleGenerate() {
    bootstrap.mutate(undefined, {
      onSuccess: (data) => setToken(data),
    });
  }

  function handleCopy() {
    if (!token) return;
    void navigator.clipboard.writeText(buildComposeSnippet(token, name));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function handleClose() {
    setToken(null);
    setName("");
    setCopied(false);
    onClose();
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add an agent</DialogTitle>
          <DialogDescription>
            Generate a single-use bootstrap token and a compose snippet to
            register a new agent host.
          </DialogDescription>
        </DialogHeader>
        {!token ? (
          <div className="space-y-4 py-2">
            <p className="text-sm text-text-secondary">
              Generate a one-time bootstrap token and a ready-to-paste compose
              snippet for a new agent host. Token is valid for 24 hours.
            </p>
            <div className="space-y-1.5">
              <Label htmlFor="agent-display-name">
                Display name (optional)
              </Label>
              <Input
                id="agent-display-name"
                placeholder="e.g. analytics-prod-1"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <p className="text-2xs text-text-tertiary">
                Shown in charts and lists. Defaults to the host name if left
                blank, and cannot be changed after the agent registers.
              </p>
            </div>
            <Button
              onClick={handleGenerate}
              disabled={bootstrap.isPending}
              className="w-full"
            >
              {bootstrap.isPending ? "Generating…" : "Generate snippet"}
            </Button>
          </div>
        ) : (
          <div className="space-y-4 py-2">
            <p className="text-sm text-text-secondary">
              On the new agent host, save the snippet below as
              <code className="mx-1 rounded bg-[var(--bg-code)] px-1 py-0.5 font-mono text-xs text-[var(--text-code)]">
                docker-compose.yml
              </code>
              and run
              <code className="mx-1 rounded bg-[var(--bg-code)] px-1 py-0.5 font-mono text-xs text-[var(--text-code)]">
                docker compose up -d
              </code>
              .
            </p>
            <p className="text-xs text-[var(--status-running)] font-medium">
              This is the only time this token will be shown.
            </p>
            <div className="relative">
              <pre
                className="overflow-x-auto rounded-md border border-[var(--border-subtle)] bg-[var(--bg-code)] p-3 font-mono text-xs text-[var(--text-code)]"
                data-testid="agent-compose-snippet"
              >
                {buildComposeSnippet(token, name)}
              </pre>
              <Button
                variant="ghost"
                size="icon"
                className="absolute right-2 top-2 size-7"
                onClick={handleCopy}
                aria-label="Copy compose snippet"
              >
                {copied ? (
                  <RefreshCw className="size-3.5 text-[var(--status-success)]" />
                ) : (
                  <Copy className="size-3.5" />
                )}
              </Button>
            </div>
            <p className="text-2xs text-text-tertiary">
              Token expires: {new Date(token.expires_at).toLocaleString()}
            </p>
            <Button variant="outline" className="w-full" onClick={handleClose}>
              Done
            </Button>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

interface CreateComputeModalProps {
  open: boolean;
  onClose: () => void;
}

function CreateComputeModal({ open, onClose }: CreateComputeModalProps) {
  const { data: options, isLoading } = useComputeOptions();
  const create = useCreateElasticAgent();
  const [cpu, setCpu] = useState<number | null>(null);
  const [memory, setMemory] = useState<number | null>(null);
  const [idleMinutes, setIdleMinutes] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const currency = options?.currency ?? null;
  // Initialize the sliders from the server ranges once options load.
  const cpuValue = cpu ?? options?.cpu_min ?? 1;
  const memoryValue = memory ?? options?.memory_min_gb ?? 1;
  const idleValue = idleMinutes ?? options?.default_idle_minutes ?? 15;
  const hourlyCost = options
    ? cpuValue * options.price_vcpu_hour +
      memoryValue * options.price_memory_gb_hour
    : 0;

  function handleClose() {
    setCpu(null);
    setMemory(null);
    setIdleMinutes(null);
    setName("");
    setError(null);
    onClose();
  }

  async function handleCreate() {
    setError(null);
    try {
      await create.mutateAsync({
        cpu: cpuValue,
        memory_gb: memoryValue,
        idle_timeout_minutes: idleValue,
        name: name.trim() || undefined,
      });
      handleClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create compute");
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>New compute</DialogTitle>
          <DialogDescription>
            Choose vCPU and memory — you pay the resulting hourly rate only
            while it runs, and it terminates automatically after the idle
            timeout.
          </DialogDescription>
        </DialogHeader>

        {isLoading ? (
          <div className="space-y-3 py-2">
            <Skeleton className="h-9 w-full rounded" />
            <Skeleton className="h-20 w-full rounded" />
          </div>
        ) : !options?.enabled ? (
          <p className="py-4 text-sm text-text-secondary">
            Elastic compute is not enabled on this control plane. Set{" "}
            <code className="rounded bg-[var(--bg-code)] px-1 py-0.5 font-mono text-xs text-[var(--text-code)]">
              ELASTIC_COMPUTE_ENABLED=true
            </code>{" "}
            to provision agents on demand.
          </p>
        ) : (
          <div className="space-y-4 py-2">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="compute-cpu">vCPU</Label>
                <span className="font-mono font-tabular text-sm">
                  {cpuValue}
                </span>
              </div>
              <input
                id="compute-cpu"
                type="range"
                aria-label="vCPU"
                min={options.cpu_min}
                max={options.cpu_max}
                step={options.cpu_step}
                value={cpuValue}
                onChange={(e) => setCpu(Number(e.target.value))}
                className="w-full accent-[var(--status-running)]"
              />
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="compute-memory">Memory (GB)</Label>
                <span className="font-mono font-tabular text-sm">
                  {memoryValue} GB
                </span>
              </div>
              <input
                id="compute-memory"
                type="range"
                aria-label="memory"
                min={options.memory_min_gb}
                max={options.memory_max_gb}
                step={options.memory_step_gb}
                value={memoryValue}
                onChange={(e) => setMemory(Number(e.target.value))}
                className="w-full accent-[var(--status-running)]"
              />
            </div>

            <div
              className="rounded-md border border-[var(--border-subtle)] bg-[var(--bg-surface)]/60 p-3"
              data-testid="compute-cost-summary"
            >
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-sm text-text-secondary">
                  <Cpu className="size-4" />
                  {cpuValue} vCPU · {memoryValue} GB
                </span>
                {currency != null && (
                  <span className="font-mono font-tabular text-sm font-medium">
                    ≈ {formatCost(hourlyCost, currency)}
                  </span>
                )}
              </div>
              {currency != null && (
                <p className="mt-1.5 text-2xs text-text-tertiary">
                  Approx.{" "}
                  {formatCost(hourlyCost * 24, currency).replace("/hr", "/day")}{" "}
                  if left running continuously.
                </p>
              )}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="compute-idle">
                Auto-terminate after idle (minutes)
              </Label>
              <Input
                id="compute-idle"
                type="number"
                min={1}
                value={idleValue}
                // Number("") is 0 and Number("x") is NaN; ?? catches neither, so
                // emptying the box to retype posted 0 and the schema rejected it
                // (ge=1) with a 422 mid-edit. null falls through to the default.
                onChange={(e) => {
                  const next = Number(e.target.value);
                  setIdleMinutes(
                    e.target.value === "" || Number.isNaN(next) ? null : next,
                  );
                }}
                className="w-32"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="compute-name">Name (optional)</Label>
              <Input
                id="compute-name"
                placeholder="e.g. warehouse-a"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>

            {error && (
              <p className="text-xs text-[var(--status-failed)]">{error}</p>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            Cancel
          </Button>
          <Button
            onClick={handleCreate}
            disabled={!options?.enabled || create.isPending}
          >
            {create.isPending ? (
              <span className="flex items-center gap-1.5">
                <Loader2 className="size-3.5 animate-spin" />
                Creating…
              </span>
            ) : (
              "Create compute"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function AgentsPage() {
  const { data: computeOptions } = useComputeOptions();
  const currency = computeOptions?.currency ?? null;
  const { data: agents = [], isLoading } = useAdminAgents();
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [bootstrapOpen, setBootstrapOpen] = useState(false);
  const [computeOpen, setComputeOpen] = useState(false);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-6 py-3 shrink-0">
        <p className="text-xs text-text-secondary font-tabular">
          {plural(agents.length, "agent")}
        </p>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            className="h-8 text-xs"
            onClick={() => setComputeOpen(true)}
          >
            New compute
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-8 text-xs"
            onClick={() => setBootstrapOpen(true)}
          >
            Generate bootstrap
          </Button>
        </div>
      </div>

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
        ) : agents.length === 0 ? (
          <EmptyState
            icon={Server}
            title="No agents connected"
            description="Generate a bootstrap token to connect an agent host."
            action={
              <Button
                size="sm"
                className="h-8 text-xs"
                onClick={() => setBootstrapOpen(true)}
              >
                Generate bootstrap
              </Button>
            }
          />
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-[var(--bg-surface)] z-10">
              <tr className="border-b border-[var(--border-subtle)]">
                {[
                  "Status",
                  "Name",
                  "DuckDB",
                  "Host",
                  "Extensions",
                  "Mem",
                  "Cost",
                  "Last ping",
                ].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-2 text-left text-xs font-medium text-text-secondary"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {agents.map((agent, i) => (
                <tr
                  key={agent.id}
                  onClick={() => setSelectedAgent(agent)}
                  className={cn(
                    "cursor-pointer border-b border-[var(--border-subtle)] hover:bg-accent/50",
                    i % 2 === 0 ? "" : "bg-[var(--bg-surface)]/40",
                  )}
                >
                  <td className="px-4 py-2">
                    {(() => {
                      // Convey the transitional lifecycle (e.g. "provisioning")
                      // to assistive tech, not the raw socket status.
                      const label =
                        agent.lifecycle && agent.lifecycle !== "running"
                          ? agent.lifecycle
                          : agent.status;
                      return (
                        <span
                          className={cn(
                            "size-2.5 rounded-full inline-block",
                            agentDotClass(agent),
                          )}
                          role="img"
                          aria-label={label}
                          title={label}
                        />
                      );
                    })()}
                  </td>
                  <td className="px-4 py-2 font-medium">
                    <span className="flex items-center gap-2">
                      {agent.name}
                      {agent.lifecycle && agent.lifecycle !== "running" && (
                        <span className="rounded bg-[var(--bg-surface)] px-1.5 py-0.5 text-2xs font-normal text-text-tertiary">
                          {agent.lifecycle}
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="px-4 py-2 font-mono text-xs">
                    {agent.capabilities?.duckdb_version ?? "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-text-secondary">
                    {agent.capabilities?.host ?? "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-2xs text-text-tertiary max-w-[180px] truncate">
                    {agent.capabilities?.extensions.join(", ") ?? "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs font-tabular">
                    {agent.capabilities
                      ? `${agent.capabilities.memory_limit_gb} GB`
                      : agent.requested_memory_gb != null
                        ? `${agent.requested_memory_gb} GB`
                        : "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs font-tabular">
                    {agent.hourly_cost != null && currency != null
                      ? formatCost(agent.hourly_cost, currency)
                      : "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-2xs text-text-tertiary">
                    {relativeTime(agent.last_ping_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <AgentDrawer
        agent={selectedAgent}
        open={!!selectedAgent}
        onClose={() => setSelectedAgent(null)}
      />
      <BootstrapModal
        open={bootstrapOpen}
        onClose={() => setBootstrapOpen(false)}
      />
      <CreateComputeModal
        open={computeOpen}
        onClose={() => setComputeOpen(false)}
      />
    </div>
  );
}
