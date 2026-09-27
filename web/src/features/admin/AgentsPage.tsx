import { useMemo, useState } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import { Copy, Cpu, Loader2, RefreshCw, Search, Server } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader, PageToolbar } from "@/components/ui/page-header";
import { Segmented } from "@/components/ui/segmented";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useAdminAgents,
  useBootstrapAgent,
  useComputeOptions,
  useCreateElasticAgent,
} from "@/queries/agents";
import { useMe } from "@/queries/auth";
import { RuntimeBadge } from "@/components/app/RuntimeBadge";
import {
  runtimeLabel,
  type Agent,
  type AgentAccessMode,
  type BootstrapToken,
  type Runtime,
} from "@/types/agent";
import { cn, plural } from "@/utils";
import { agentDotClass, formatCost, relativeTime } from "./agentFormat";

type FleetFilter = "active" | "stopped" | "all";

// Up or on its way up (or down): an agent a run could reach or soon will. Every
// other agent is stopped — terminated, failed, or an offline static host — and
// a long-lived fleet accumulates those, so the page opens on the active ones.
export function isActiveAgent(agent: Agent): boolean {
  return (
    agent.status !== "unavailable" ||
    agent.lifecycle === "provisioning" ||
    agent.lifecycle === "terminating"
  );
}

const STATUS_RANK: Record<Agent["status"], number> = {
  healthy: 0,
  degraded: 1,
  unavailable: 2,
};

function fleetOrder(a: Agent, b: Agent): number {
  const active = Number(isActiveAgent(b)) - Number(isActiveAgent(a));
  if (active) return active;
  const rank = STATUS_RANK[a.status] - STATUS_RANK[b.status];
  return rank || a.name.localeCompare(b.name);
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

/**
 * Which runtime — DuckDB line plus its extensions — an agent will run. The
 * deployment's default comes first and is preselected by the caller; a beta
 * runtime is labelled so nobody picks a pre-release DuckDB unawares.
 */
function RuntimeSelect({
  id,
  runtimes,
  value,
  onChange,
}: {
  id: string;
  runtimes: Runtime[];
  value: string;
  onChange: (runtimeId: string) => void;
}) {
  const ordered = [...runtimes].sort(
    (a, b) => Number(b.default) - Number(a.default),
  );
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id} aria-label="Runtime">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {ordered.map((r) => (
          <SelectItem key={r.id} value={r.id}>
            {r.display_name}
            {r.status === "beta" ? " (Beta)" : ""}
            {r.default ? " — default" : ""}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

interface BootstrapModalProps {
  open: boolean;
  onClose: () => void;
}

function BootstrapModal({ open, onClose }: BootstrapModalProps) {
  const bootstrap = useBootstrapAgent();
  const { data: options } = useComputeOptions();
  const runtimes = options?.runtimes ?? [];
  const [token, setToken] = useState<BootstrapToken | null>(null);
  const [name, setName] = useState("");
  const [runtimeId, setRuntimeId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const chosenRuntime = runtimeId ?? options?.default_runtime ?? null;

  function handleGenerate() {
    bootstrap.mutate(chosenRuntime ?? undefined, {
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
    setRuntimeId(null);
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
            {runtimes.length > 1 && chosenRuntime && (
              <div className="space-y-1.5">
                <Label htmlFor="bootstrap-runtime">Runtime</Label>
                <RuntimeSelect
                  id="bootstrap-runtime"
                  runtimes={runtimes}
                  value={chosenRuntime}
                  onChange={setRuntimeId}
                />
                <p className="text-2xs text-text-tertiary">
                  The DuckDB version and extensions the agent's image carries.
                  Its image tag in the snippet follows this choice.
                </p>
              </div>
            )}
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
  const [accessMode, setAccessMode] = useState<AgentAccessMode>("open");
  const [runtimeId, setRuntimeId] = useState<string | null>(null);
  const [betaAccepted, setBetaAccepted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runtimes = options?.runtimes ?? [];
  const chosenRuntimeId = runtimeId ?? options?.default_runtime ?? null;
  const chosenRuntime = runtimes.find((r) => r.id === chosenRuntimeId) ?? null;
  const isBeta = chosenRuntime?.status === "beta";

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
    setAccessMode("open");
    setRuntimeId(null);
    setBetaAccepted(false);
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
        access_mode: accessMode,
        runtime_id: chosenRuntimeId ?? undefined,
        allow_beta: isBeta ? true : undefined,
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

            {/* Set here rather than only on the Access tab: an agent created open
                registers and starts taking work immediately, so narrowing it
                afterwards leaves a window where anyone could use it. */}
            <div className="space-y-1.5">
              <Label htmlFor="compute-access">Who can use it</Label>
              <Select
                value={accessMode}
                onValueChange={(v) => setAccessMode(v as AgentAccessMode)}
              >
                <SelectTrigger id="compute-access" aria-label="Who can use it">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="open">Anyone signed in</SelectItem>
                  <SelectItem value="restricted">
                    Only people I grant access
                  </SelectItem>
                </SelectContent>
              </Select>
              <p className="text-2xs text-text-tertiary">
                {accessMode === "restricted"
                  ? "Nobody else sees this agent until you grant access from its Access tab. You keep full access as an agent admin."
                  : "Any signed-in user can run work on this agent. You can restrict it later from its Access tab."}
              </p>
            </div>

            {runtimes.length > 1 && chosenRuntimeId && (
              <div className="space-y-1.5">
                <Label htmlFor="compute-runtime">Runtime</Label>
                <RuntimeSelect
                  id="compute-runtime"
                  runtimes={runtimes}
                  value={chosenRuntimeId}
                  onChange={(id) => {
                    setRuntimeId(id);
                    setBetaAccepted(false);
                  }}
                />
                <p className="text-2xs text-text-tertiary">
                  The DuckDB version and extensions it runs. It keeps this
                  runtime every time it restarts.
                </p>
                {isBeta && (
                  <label
                    htmlFor="compute-beta"
                    className="flex items-start gap-2 rounded-md border border-[var(--status-running)]/40 p-2 text-2xs text-text-secondary"
                  >
                    <Checkbox
                      id="compute-beta"
                      checked={betaAccepted}
                      onCheckedChange={(v) => setBetaAccepted(v === true)}
                      className="mt-0.5"
                    />
                    <span>
                      {chosenRuntime?.display_name} is in beta. Only work that
                      names this agent runs on it, and SQL that only this DuckDB
                      version understands may still be refused.
                    </span>
                  </label>
                )}
              </div>
            )}

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
            disabled={
              !options?.enabled || create.isPending || (isBeta && !betaAccepted)
            }
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
  const { ws } = useParams({ from: "/$ws/compute" });
  // Adding compute is a fleet-level spend decision, so it stays on the global
  // permission — a per-agent grant, however high, never confers it.
  const { data: me } = useMe();
  const canManageFleet = (me?.permissions ?? []).includes("agents:manage");
  const [filter, setFilter] = useState<FleetFilter>("active");
  const [search, setSearch] = useState("");

  const activeCount = agents.filter(isActiveAgent).length;
  const shown = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return agents
      .filter((a) =>
        filter === "all"
          ? true
          : filter === "active"
            ? isActiveAgent(a)
            : !isActiveAgent(a),
      )
      .filter(
        (a) =>
          !needle ||
          a.name.toLowerCase().includes(needle) ||
          (a.capabilities?.host ?? "").toLowerCase().includes(needle),
      )
      .sort(fleetOrder);
  }, [agents, filter, search]);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title="Compute"
        description={`${plural(agents.length, "agent")} · ${activeCount} active`}
        actions={
          canManageFleet && (
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
          )
        }
      />
      {agents.length > 0 && (
        <PageToolbar>
          <Segmented
            label="Show"
            hideLabel
            value={filter}
            onChange={setFilter}
            options={[
              { value: "active", label: `Active (${activeCount})` },
              {
                value: "stopped",
                label: `Stopped (${agents.length - activeCount})`,
              },
              { value: "all", label: `All (${agents.length})` },
            ]}
          />
          <div className="relative w-56">
            <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-text-tertiary" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name or host…"
              aria-label="Search agents"
              className="h-8 pl-7 text-xs"
            />
          </div>
        </PageToolbar>
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
        ) : agents.length === 0 ? (
          <EmptyState
            icon={Server}
            title={
              canManageFleet ? "No agents connected" : "No agents available"
            }
            description={
              canManageFleet
                ? "Generate a bootstrap token to connect an agent host."
                : "You have not been granted access to any agent yet."
            }
            action={
              canManageFleet ? (
                <Button
                  size="sm"
                  className="h-8 text-xs"
                  onClick={() => setBootstrapOpen(true)}
                >
                  Generate bootstrap
                </Button>
              ) : undefined
            }
          />
        ) : shown.length === 0 ? (
          <p className="px-6 py-4 text-sm text-text-tertiary">
            {search.trim()
              ? "No agents match that search."
              : filter === "active"
                ? "No agent is running. Stopped agents are under Stopped."
                : "No stopped agents."}
          </p>
        ) : (
          <table className="table-gutter w-full text-sm">
            <thead className="sticky top-0 bg-[var(--bg-surface)] z-10">
              <tr className="border-b border-[var(--border-subtle)]">
                {[
                  "Status",
                  "Name",
                  "Runtime",
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
              {shown.map((agent, i) => (
                <tr
                  key={agent.id}
                  onClick={() =>
                    navigate({
                      to: "/$ws/compute/$agentId",
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
                  <td className="px-4 py-2 text-xs">
                    <span className="flex items-center gap-1.5">
                      <span className="font-mono">
                        {runtimeLabel(agent) ?? "—"}
                      </span>
                      <RuntimeBadge agent={agent} />
                    </span>
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
