import { useState } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import { Copy, Cpu, Loader2, RefreshCw, Server } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/app/EmptyState";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useAdminAgents,
  useBootstrapAgent,
  useComputeOptions,
  useCreateElasticAgent,
} from "@/queries/agents";
import type { BootstrapToken } from "@/types/agent";
import { cn, plural } from "@/utils";
import { agentDotClass, formatCost, relativeTime } from "./agentFormat";

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
  const [bootstrapOpen, setBootstrapOpen] = useState(false);
  const [computeOpen, setComputeOpen] = useState(false);
  const navigate = useNavigate();
  const { ws } = useParams({ from: "/$ws/admin/agents" });

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
                  onClick={() =>
                    navigate({
                      to: "/$ws/admin/agents/$agentId",
                      params: { ws, agentId: agent.id },
                    })
                  }
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
