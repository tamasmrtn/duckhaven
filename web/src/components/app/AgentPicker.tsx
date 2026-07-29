import { useState } from "react";
import {
  ChevronsUpDown,
  CheckCircle2,
  Circle,
  AlertCircle,
  Power,
  RotateCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  useAgents,
  useRestartAgent,
  useTerminateAgent,
} from "@/queries/agents";
import type { Agent, AgentStatus } from "@/types/agent";
import type { BackendKind } from "@/types/storage-backend";
import { agentSupportsBackend, agentTierAtLeast } from "@/types/agent";
import { cn } from "@/utils";

/**
 * No `minTier` prop: both call sites (the worksheet and the schedule dialog)
 * need exactly `use`, and the server already filters `/agents` to what the
 * caller may target — so the list is correct for both without configuration.
 */
interface AgentPickerProps {
  value: string | null;
  onChange: (agentId: string) => void;
  workspaceBackend?: BackendKind;
}

const statusIcon: Record<AgentStatus, React.ReactNode> = {
  healthy: <CheckCircle2 className="size-3 text-[var(--status-success)]" />,
  degraded: <AlertCircle className="size-3 text-[var(--status-running)]" />,
  unavailable: <Circle className="size-3 text-[var(--status-failed)]" />,
};

function AgentRow({ agent, backend }: { agent: Agent; backend?: BackendKind }) {
  const compatible = !backend || agentSupportsBackend(agent, backend);
  return (
    <div className={cn("flex flex-col gap-0.5", !compatible && "opacity-60")}>
      <div className="flex items-center gap-1.5">
        {statusIcon[agent.status]}
        <span className="font-medium text-sm">{agent.name}</span>
        <span className="ml-auto font-mono text-2xs text-text-secondary">
          {agent.capabilities
            ? `DuckDB ${agent.capabilities.duckdb_version} · ${agent.capabilities.memory_limit_gb} GB`
            : "provisioning…"}
        </span>
      </div>
      <div className="flex gap-1 pl-4.5">
        {agent.capabilities?.host && (
          <span className="text-2xs text-text-tertiary">
            {agent.capabilities.host}
          </span>
        )}
        {["s3", "adls_gen2", "object_store"].map((ext) => {
          // object_store is MinIO-backed (S3), so it also needs httpfs.
          const extensions = agent.capabilities?.extensions ?? [];
          const supported =
            ext === "adls_gen2"
              ? extensions.includes("azure")
              : extensions.includes("httpfs");
          return (
            <span
              key={ext}
              className={cn(
                "text-2xs",
                supported
                  ? "text-[var(--status-success)]"
                  : "text-[var(--status-failed)]",
              )}
            >
              {ext === "s3" ? "S3" : ext === "adls_gen2" ? "ADLS" : "object"}{" "}
              {supported ? "✓" : "✗"}
            </span>
          );
        })}
      </div>
      {!compatible && backend && (
        <p className="pl-4.5 text-2xs text-[var(--status-failed)]">
          Missing extension for {backend === "adls_gen2" ? "azure" : "httpfs"}
        </p>
      )}
    </div>
  );
}

export function AgentPicker({
  value,
  onChange,
  workspaceBackend,
}: AgentPickerProps) {
  const [open, setOpen] = useState(false);
  const { data: agents = [] } = useAgents();
  const terminate = useTerminateAgent();
  const restart = useRestartAgent();
  const selected = agents.find((a) => a.id === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="h-8 w-[220px] justify-between gap-1 text-sm"
        >
          {selected ? (
            <span className="flex items-center gap-1.5 truncate">
              {statusIcon[selected.status]}
              {selected.name}
              <span className="ml-1 font-mono text-2xs text-text-secondary">
                {selected.capabilities?.memory_limit_gb ??
                  selected.requested_memory_gb ??
                  "—"}{" "}
                GB
              </span>
            </span>
          ) : (
            <span className="text-text-tertiary">Select agent…</span>
          )}
          <ChevronsUpDown className="ml-auto size-3.5 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[320px] p-0" align="start">
        <Command>
          <CommandInput placeholder="Search agents…" className="h-9" />
          <CommandList>
            <CommandEmpty>
              No agents found. Add one from Admin → Agents.
            </CommandEmpty>
            <CommandGroup>
              {agents.map((agent) => (
                <CommandItem
                  key={agent.id}
                  value={agent.id}
                  onSelect={() => {
                    if (agent.status !== "unavailable") {
                      onChange(agent.id);
                      setOpen(false);
                    }
                  }}
                  // Static unavailable agents stay unselectable; elastic agents are
                  // never disabled so their manage controls remain clickable.
                  disabled={!agent.provider && agent.status === "unavailable"}
                  className="flex flex-col items-start py-2"
                >
                  <AgentRow agent={agent} backend={workspaceBackend} />
                  {/* Per-agent, not per-user: someone can hold `operate` on the
                      team's warehouse and only `use` on everything else. */}
                  {agentTierAtLeast(agent, "operate") && agent.provider && (
                    <div className="mt-1.5 flex gap-1.5 pl-4.5">
                      {(agent.lifecycle === "running" ||
                        agent.lifecycle === "provisioning") && (
                        <button
                          type="button"
                          aria-label={`terminate ${agent.name}`}
                          onMouseDown={(e) => e.stopPropagation()}
                          onClick={(e) => {
                            e.stopPropagation();
                            terminate.mutate(agent.id);
                          }}
                          className="flex items-center gap-1 rounded border border-[var(--border-subtle)] px-1.5 py-0.5 text-2xs text-text-secondary hover:text-text-primary"
                        >
                          <Power className="size-3" /> Terminate
                        </button>
                      )}
                      {(agent.lifecycle === "terminated" ||
                        agent.lifecycle === "failed") && (
                        <button
                          type="button"
                          aria-label={`restart ${agent.name}`}
                          onMouseDown={(e) => e.stopPropagation()}
                          onClick={(e) => {
                            e.stopPropagation();
                            restart.mutate(agent.id);
                          }}
                          className="flex items-center gap-1 rounded border border-[var(--border-subtle)] px-1.5 py-0.5 text-2xs text-text-secondary hover:text-text-primary"
                        >
                          <RotateCw className="size-3" /> Restart
                        </button>
                      )}
                    </div>
                  )}
                </CommandItem>
              ))}
            </CommandGroup>
            {workspaceBackend && (
              <div className="border-t p-2 text-2xs text-text-secondary">
                This workspace uses{" "}
                {workspaceBackend === "adls_gen2"
                  ? "ADLS Gen 2"
                  : workspaceBackend.toUpperCase()}
              </div>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
