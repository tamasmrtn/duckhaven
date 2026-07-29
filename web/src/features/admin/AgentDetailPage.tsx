import { useState } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Circle,
  Power,
  RotateCw,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
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
  useAdminAgent,
  useComputeOptions,
  useDeleteAgent,
  useRestartAgent,
  useRevokeAgent,
  useTerminateAgent,
} from "@/queries/agents";
import { useAgentMonitoring } from "@/queries/agents";
import type { Agent, AgentStatus } from "@/types/agent";
import { formatCost } from "./agentFormat";
import { MonitoringTab } from "./monitoring/MonitoringTab";

const statusIcon: Record<AgentStatus, React.ReactNode> = {
  healthy: <CheckCircle2 className="size-4 text-[var(--status-success)]" />,
  degraded: <AlertCircle className="size-4 text-[var(--status-running)]" />,
  unavailable: <Circle className="size-4 text-[var(--status-failed)]" />,
};

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-text-secondary">{label}</span>
      <span className="font-mono text-xs font-tabular text-right">{value}</span>
    </div>
  );
}

function OverviewTab({ agent }: { agent: Agent }) {
  const { data: computeOptions } = useComputeOptions();
  const currency = computeOptions?.currency ?? null;
  const navigate = useNavigate();
  const { ws } = useParams({ from: "/$ws/admin/agents/$agentId" });
  const revoke = useRevokeAgent();
  const restart = useRestartAgent();
  const terminate = useTerminateAgent();
  const deleteAgent = useDeleteAgent();
  const [confirmDelete, setConfirmDelete] = useState(false);

  // "Recent errors" used to be a hardcoded 0. It is now the real count over the
  // shortest window, which is the one an operator checking on a live problem means.
  const { data: recent } = useAgentMonitoring(agent.id, "1h");

  const restartable =
    !!agent.provider &&
    (agent.lifecycle === "terminated" || agent.lifecycle === "failed");
  const terminable =
    !!agent.provider &&
    (agent.lifecycle === "running" || agent.lifecycle === "provisioning");

  return (
    <>
      <div className="grid gap-4 md:grid-cols-2">
        {agent.provider && (
          <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-secondary">
              Elastic compute
            </p>
            <div className="space-y-1 text-sm">
              <Field label="Lifecycle" value={agent.lifecycle ?? "—"} />
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
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-secondary">
            Capabilities
          </p>
          {agent.capabilities ? (
            <div className="space-y-1 text-sm">
              <Field label="DuckDB" value={agent.capabilities.duckdb_version} />
              <Field
                label="Memory cap"
                value={`${agent.capabilities.memory_limit_gb} GB`}
              />
              <Field label="Cores" value={agent.capabilities.cores} />
              {agent.capabilities.host && (
                <Field label="Host" value={agent.capabilities.host} />
              )}
              {agent.capabilities.tailscale_ip && (
                <Field
                  label="Tailscale IP"
                  value={agent.capabilities.tailscale_ip}
                />
              )}
              <Field
                label="Extensions"
                value={
                  <span className="block max-w-[240px] truncate">
                    {agent.capabilities.extensions.join(", ")}
                  </span>
                }
              />
            </div>
          ) : (
            <p className="text-sm text-text-tertiary">
              Not yet reported — the agent has not registered.
            </p>
          )}
        </section>

        <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-secondary">
            Last hour
          </p>
          <div className="space-y-1 text-sm">
            <Field label="Completed" value={recent?.summary.completed ?? "—"} />
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
          onClick={() =>
            navigate({
              to: "/$ws/history",
              params: { ws },
              search: { agent: agent.id },
            })
          }
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
                    navigate({ to: "/$ws/admin/agents", params: { ws } });
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
  const { ws, agentId } = useParams({ from: "/$ws/admin/agents/$agentId" });
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
          onClick={() => navigate({ to: "/$ws/admin/agents", params: { ws } })}
        >
          Back to agents
        </Button>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center gap-3 border-b border-[var(--border-subtle)] px-6 py-3 shrink-0">
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          aria-label="back to agents"
          onClick={() => navigate({ to: "/$ws/admin/agents", params: { ws } })}
        >
          <ArrowLeft className="size-4" />
        </Button>
        {statusIcon[agent.status]}
        <h2 className="text-md font-semibold">{agent.name}</h2>
        {agent.lifecycle && agent.lifecycle !== "running" && (
          <span className="rounded bg-[var(--bg-surface)] px-1.5 py-0.5 text-2xs text-text-tertiary">
            {agent.lifecycle}
          </span>
        )}
      </div>

      <Tabs
        defaultValue="monitoring"
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <TabsList className="m-4 mb-0 h-8 w-fit shrink-0">
          <TabsTrigger value="monitoring" className="text-xs">
            Monitoring
          </TabsTrigger>
          <TabsTrigger value="overview" className="text-xs">
            Overview
          </TabsTrigger>
        </TabsList>
        <TabsContent
          value="monitoring"
          className="mt-0 min-h-0 flex-1 overflow-auto p-4"
        >
          <MonitoringTab ws={ws} agent={agent} />
        </TabsContent>
        <TabsContent
          value="overview"
          className="mt-0 min-h-0 flex-1 overflow-auto p-4"
        >
          <OverviewTab agent={agent} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
