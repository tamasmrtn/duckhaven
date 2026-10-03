import { useState } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Circle,
  Info,
  Power,
  RotateCw,
  Trash2,
  Unplug,
} from "lucide-react";
import { RuntimeBadge } from "@/components/app/RuntimeBadge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useAdminAgent,
  useComputeOptions,
  useDeleteAgent,
  useDisconnectAgent,
  useRestartAgent,
  useRevokeAgent,
  useRuntimes,
  useTerminateAgent,
} from "@/queries/agents";
import { useAgentMonitoring } from "@/queries/agents";
import type { Agent, AgentStatus } from "@/types/agent";
import {
  agentRestartable,
  agentTierAtLeast,
  runtimeRefusal,
} from "@/types/agent";
import { AgentAccessTab } from "./AgentAccessTab";
import { formatCost } from "./agentFormat";
import { MonitoringTab } from "./monitoring/MonitoringTab";
import { SectionLabel } from "@/components/ui/section-label";

const statusIcon: Record<AgentStatus, React.ReactNode> = {
  healthy: <CheckCircle2 className="size-4 text-[var(--status-success)]" />,
  degraded: <AlertCircle className="size-4 text-[var(--status-running)]" />,
  unavailable: <Circle className="size-4 text-[var(--status-failed)]" />,
};

/** An ⓘ next to a field's label that explains it on hover or keyboard focus. */
function FieldHelp({ label, help }: { label: string; help: string }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={`What is ${label}?`}
            className="rounded-sm text-text-tertiary hover:text-text-secondary focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
          >
            <Info className="size-3.5" />
          </button>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs text-xs">{help}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function FieldLabel({ label, help }: { label: string; help?: string }) {
  return (
    <span className="flex items-center gap-1.5 text-text-secondary">
      {label}
      {help && <FieldHelp label={label} help={help} />}
    </span>
  );
}

function Field({
  label,
  value,
  help,
}: {
  label: string;
  value: React.ReactNode;
  help?: string;
}) {
  return (
    <div className="flex justify-between gap-4">
      <FieldLabel label={label} help={help} />
      <span className="font-mono text-xs font-tabular text-right">{value}</span>
    </div>
  );
}

// What each capability means, for the ⓘ beside it. Written for someone who
// knows what a query is but not how an agent is put together.
const CAPABILITY_HELP = {
  runtime:
    "The DuckDB version and extensions this agent's image was built with. DuckHaven only sends work to agents on a runtime it supports, and never picks one on a beta runtime unless you choose that agent.",
  duckdb: "The exact DuckDB engine version the agent is running.",
  sandbox:
    "Whether DuckDB's configuration lock applied here. Locked means a query can't change the agent's security settings. If the lock failed, SQL sessions are refused on this agent; \"lock off\" means an operator turned it off.",
  memory:
    "The memory this agent can use for queries: its container's memory limit, or the machine's total memory when the container has none.",
  cores:
    "The CPU cores this agent runs queries on: its container's CPU limit, or the machine's cores when the container has none.",
  host: "The name of the machine or container the agent runs on.",
  extensions:
    "The DuckDB extensions loaded on this agent. Each catalog and storage type needs its own: httpfs for S3 and the bundled object store, azure for ADLS, iceberg for Iceberg catalogs, and ducklake with postgres_scanner for DuckLake.",
} as const;

/**
 * Why this agent's runtime needs attention, in a sentence, or null. Deprecated
 * runtimes still run but are closed to new compute; the upstream end-of-support
 * date says how long that stays comfortable.
 */
function RuntimeNotice({ agent }: { agent: Agent }) {
  const { data: runtimes = [] } = useRuntimes();
  const refusal = runtimeRefusal(agent);
  if (refusal) {
    return <Banner>{refusal}. The control plane sends it no work.</Banner>;
  }
  if (agent.runtime?.status !== "deprecated") return null;
  const eol = runtimes.find((r) => r.id === agent.runtime?.id)?.upstream_eol;
  return (
    <Banner>
      {agent.runtime.display_name} is deprecated
      {eol ? ` (upstream support ended ${eol})` : ""}. It keeps running, but new
      compute can't use it — move this workload to a current runtime.
    </Banner>
  );
}

const SANDBOX_LABEL = {
  verified: "locked",
  disabled: "lock off (operator)",
  failed: "lock failed — no SQL sessions",
} as const;

function OverviewTab({ agent }: { agent: Agent }) {
  const { data: computeOptions } = useComputeOptions();
  const currency = computeOptions?.currency ?? null;
  const navigate = useNavigate();
  const { ws } = useParams({ from: "/$ws/compute/$agentId" });
  const revoke = useRevokeAgent();
  const restart = useRestartAgent();
  const terminate = useTerminateAgent();
  const disconnect = useDisconnectAgent();
  const deleteAgent = useDeleteAgent();
  const [confirmDelete, setConfirmDelete] = useState(false);

  // Lifecycle actions need `operate`, destroying the agent needs `admin`. The
  // API enforces both; hiding them keeps the page honest about what this user
  // can actually do rather than offering a button that 403s.
  const canOperate = agentTierAtLeast(agent, "operate");
  const canAdminister = agentTierAtLeast(agent, "admin");

  // "Recent errors" used to be a hardcoded 0. It is now the real count over the
  // shortest window, which is the one an operator checking on a live problem means.
  const { data: recent } = useAgentMonitoring(agent.id, { window: "1h" });

  // Never on a retired runtime: it can't start again.
  const restartable = canOperate && agentRestartable(agent);
  const terminable =
    canOperate &&
    !!agent.provider &&
    (agent.lifecycle === "running" || agent.lifecycle === "provisioning");

  return (
    <>
      <RuntimeNotice agent={agent} />
      <div className="grid gap-4 md:grid-cols-2">
        {agent.provider && (
          <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
            <SectionLabel className="mb-2">Elastic compute</SectionLabel>
            <div className="space-y-1 text-sm">
              <Field label="Lifecycle" value={agent.lifecycle ?? "—"} />
              {agent.runtime?.display_name && (
                <Field label="Runtime" value={agent.runtime.display_name} />
              )}
              {agent.requested_cpu != null &&
                agent.requested_memory_gb != null && (
                  <Field
                    label="Size"
                    value={`${agent.requested_cpu} vCPU · ${agent.requested_memory_gb} GB`}
                  />
                )}
              {agent.hourly_cost != null && currency != null && (
                <Field
                  label="Cost"
                  value={formatCost(agent.hourly_cost, currency)}
                />
              )}
              <Field
                label="Idle timeout"
                value={
                  agent.idle_timeout_minutes != null
                    ? `${agent.idle_timeout_minutes} min`
                    : "default"
                }
              />
            </div>
          </section>
        )}

        <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
          <SectionLabel className="mb-2">Capabilities</SectionLabel>
          {agent.capabilities ? (
            <div className="space-y-1 text-sm">
              <Field
                label="Runtime"
                help={CAPABILITY_HELP.runtime}
                value={
                  <span className="flex items-center justify-end gap-1.5">
                    {agent.runtime?.display_name ??
                      agent.runtime?.id ??
                      "unknown"}
                    <RuntimeBadge agent={agent} />
                  </span>
                }
              />
              <Field
                label="DuckDB"
                help={CAPABILITY_HELP.duckdb}
                value={
                  agent.capabilities.engine_version ??
                  agent.capabilities.duckdb_version
                }
              />
              {agent.capabilities.sandbox && (
                <Field
                  label="Sandbox"
                  help={CAPABILITY_HELP.sandbox}
                  value={SANDBOX_LABEL[agent.capabilities.sandbox]}
                />
              )}
              <Field
                label="Memory cap"
                help={CAPABILITY_HELP.memory}
                value={`${agent.capabilities.memory_limit_gb} GB`}
              />
              <Field
                label="Cores"
                help={CAPABILITY_HELP.cores}
                value={agent.capabilities.cores}
              />
              {agent.capabilities.host && (
                <Field
                  label="Host"
                  help={CAPABILITY_HELP.host}
                  value={agent.capabilities.host}
                />
              )}
              {/* A row of its own, wrapping: a one-line value was cut off
                  after the first few extensions. */}
              <div className="space-y-1.5 pt-1">
                <FieldLabel
                  label="Extensions"
                  help={CAPABILITY_HELP.extensions}
                />
                <ul aria-label="Extensions" className="flex flex-wrap gap-1">
                  {agent.capabilities.extensions.map((ext) => (
                    <li
                      key={ext}
                      className="rounded bg-[var(--bg-elevated)] px-1.5 py-0.5 font-mono text-2xs text-text-secondary"
                    >
                      {ext}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : (
            <p className="text-sm text-text-tertiary">
              Not yet reported — the agent has not registered.
            </p>
          )}
        </section>

        <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
          <SectionLabel className="mb-2">Last hour</SectionLabel>
          <div className="space-y-1 text-sm">
            <Field label="Completed" value={recent?.summary.finished ?? "—"} />
            <Field
              label="Failed"
              value={
                <span
                  className={
                    recent && recent.summary.failed > 0
                      ? "text-[var(--status-failed)]"
                      : undefined
                  }
                >
                  {recent?.summary.failed ?? "—"}
                </span>
              }
            />
          </div>
        </section>
      </div>

      <Separator className="my-4" />

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
        {canOperate && agent.status !== "unavailable" && (
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5 text-xs"
            onClick={() => disconnect.mutate(agent.id)}
            disabled={disconnect.isPending}
          >
            <Unplug className="size-3.5" />
            {disconnect.isPending ? "Disconnecting…" : "Force disconnect"}
          </Button>
        )}
        {canOperate && !agent.provider && (
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
          onClick={() =>
            navigate({
              to: "/$ws/history",
              params: { ws },
              // `user: "all"` is not optional here. History defaults to the
              // caller's own runs, so an audit link without it shows an admin
              // only the queries they personally ran on this agent.
              search: { agent: agent.id, user: "all" },
            })
          }
        >
          View audit for this agent
        </Button>
        {canAdminister && (
          <Button
            variant="destructive"
            size="sm"
            className="gap-1.5 text-xs"
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 className="size-3.5" />
            Delete
          </Button>
        )}
      </div>

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
            <li>Its monitoring history is deleted with it.</li>
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
                    navigate({ to: "/$ws/compute", params: { ws } });
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

export function AgentDetailPage() {
  const { ws, agentId } = useParams({ from: "/$ws/compute/$agentId" });
  const navigate = useNavigate();
  const { data: agent, isLoading, isError } = useAdminAgent(agentId);

  if (isLoading) {
    return (
      <div className="space-y-3 p-6">
        <Skeleton className="h-8 w-64 rounded" />
        <Skeleton className="h-40 w-full rounded-lg" />
      </div>
    );
  }

  if (isError || !agent) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <p className="text-sm font-medium">Agent not found</p>
        <p className="text-sm text-text-tertiary">
          It may have been deleted since this page was opened.
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => navigate({ to: "/$ws/compute", params: { ws } })}
        >
          Back to agents
        </Button>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        leading={
          <>
            <Button
              variant="ghost"
              size="icon"
              aria-label="back to agents"
              onClick={() => navigate({ to: "/$ws/compute", params: { ws } })}
            >
              <ArrowLeft className="size-4" />
            </Button>
            {statusIcon[agent.status]}
          </>
        }
        title={agent.name}
        badge={
          agent.lifecycle &&
          agent.lifecycle !== "running" && (
            <span className="rounded bg-[var(--bg-canvas)] px-1.5 py-0.5 text-2xs text-text-tertiary">
              {agent.lifecycle}
            </span>
          )
        }
      />

      <Tabs
        defaultValue="monitoring"
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <TabsList className="mx-6 mt-4 mb-0 h-7 w-fit shrink-0">
          <TabsTrigger value="monitoring" className="text-xs">
            Monitoring
          </TabsTrigger>
          <TabsTrigger value="overview" className="text-xs">
            Overview
          </TabsTrigger>
          {/* Only Tier 3 may read or change the ACL, so the tab is not offered
              to anyone else rather than rendering a panel that 403s. */}
          {agentTierAtLeast(agent, "admin") && (
            <TabsTrigger value="access" className="text-xs">
              Access
            </TabsTrigger>
          )}
        </TabsList>
        <TabsContent
          value="monitoring"
          className="mt-0 min-h-0 flex-1 overflow-auto px-6 py-4"
        >
          <MonitoringTab ws={ws} agent={agent} />
        </TabsContent>
        <TabsContent
          value="overview"
          className="mt-0 min-h-0 flex-1 overflow-auto px-6 py-4"
        >
          <OverviewTab agent={agent} />
        </TabsContent>
        {agentTierAtLeast(agent, "admin") && (
          <TabsContent
            value="access"
            className="mt-0 min-h-0 flex-1 overflow-auto px-6 py-4"
          >
            <AgentAccessTab agent={agent} />
          </TabsContent>
        )}
      </Tabs>
    </div>
  );
}
