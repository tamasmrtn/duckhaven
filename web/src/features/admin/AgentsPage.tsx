import { useState } from "react";
import {
  CheckCircle2,
  Circle,
  AlertCircle,
  Copy,
  RefreshCw,
  Server,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/app/EmptyState";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import {
  useAdminAgents,
  useBootstrapAgent,
  useRevokeAgent,
} from "@/queries/agents";
import type { Agent, AgentStatus, BootstrapToken } from "@/types/agent";
import { cn, plural } from "@/utils";

function buildComposeSnippet(token: BootstrapToken): string {
  return [
    "services:",
    "  duckhaven-agent:",
    `    image: ${token.agent_image}`,
    "    restart: unless-stopped",
    "    environment:",
    `      CONTROL_PLANE_URL: ${token.control_plane_url}`,
    `      BOOTSTRAP_TOKEN: ${token.token}`,
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
  const revoke = useRevokeAgent();

  if (!agent) return null;

  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent className="w-[480px] overflow-auto">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            {statusIcon[agent.status]}
            {agent.name}
          </SheetTitle>
        </SheetHeader>

        <div className="mt-6 space-y-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary mb-2">
              Capabilities
            </p>
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
          </div>

          <Separator />

          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary mb-2">
              Recent errors
            </p>
            <p className="text-sm text-text-tertiary">0</p>
          </div>

          <Separator />

          <div className="flex gap-2">
            <Button
              variant="destructive"
              size="sm"
              className="gap-1.5 text-xs"
              onClick={() => revoke.mutate(agent.id)}
              disabled={revoke.isPending}
            >
              Revoke credential
            </Button>
            <Button variant="outline" size="sm" className="gap-1.5 text-xs">
              View audit for this agent
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

interface BootstrapModalProps {
  open: boolean;
  onClose: () => void;
}

function BootstrapModal({ open, onClose }: BootstrapModalProps) {
  const bootstrap = useBootstrapAgent();
  const [token, setToken] = useState<BootstrapToken | null>(null);
  const [copied, setCopied] = useState(false);

  function handleGenerate() {
    bootstrap.mutate(undefined, {
      onSuccess: (data) => setToken(data),
    });
  }

  function handleCopy() {
    if (!token) return;
    void navigator.clipboard.writeText(buildComposeSnippet(token));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function handleClose() {
    setToken(null);
    setCopied(false);
    onClose();
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add an agent</DialogTitle>
        </DialogHeader>
        {!token ? (
          <div className="space-y-4 py-2">
            <p className="text-sm text-text-secondary">
              Generate a one-time bootstrap token and a ready-to-paste compose
              snippet for a new agent host. Token is valid for 24 hours.
            </p>
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
                {buildComposeSnippet(token)}
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

export function AgentsPage() {
  const { data: agents = [], isLoading } = useAdminAgents();
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [bootstrapOpen, setBootstrapOpen] = useState(false);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-[var(--border-subtle)] px-6 py-3 shrink-0">
        <p className="text-xs text-text-secondary font-tabular">
          {plural(agents.length, "agent")}
        </p>
        <Button
          size="sm"
          className="h-8 text-xs"
          onClick={() => setBootstrapOpen(true)}
        >
          Generate bootstrap
        </Button>
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
                    <span
                      className={cn(
                        "size-2.5 rounded-full inline-block",
                        statusDot[agent.status],
                      )}
                    />
                  </td>
                  <td className="px-4 py-2 font-medium">{agent.name}</td>
                  <td className="px-4 py-2 font-mono text-xs">
                    {agent.capabilities.duckdb_version}
                  </td>
                  <td className="px-4 py-2 text-xs text-text-secondary">
                    {agent.capabilities.host ?? "—"}
                  </td>
                  <td className="px-4 py-2 font-mono text-2xs text-text-tertiary max-w-[180px] truncate">
                    {agent.capabilities.extensions.join(", ")}
                  </td>
                  <td className="px-4 py-2 font-mono text-xs font-tabular">
                    {agent.capabilities.memory_limit_gb} GB
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
    </div>
  );
}
