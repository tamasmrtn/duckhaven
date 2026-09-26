import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  Check,
  ChevronsUpDown,
  MoreHorizontal,
  Play,
  Power,
} from "lucide-react";
import { toast } from "sonner";
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
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { StorageLabel } from "@/components/app/StorageIcon";
import {
  useAgents,
  useRestartAgent,
  useTerminateAgent,
} from "@/queries/agents";
import {
  agentAvailability,
  agentTierAtLeast,
  type Agent,
  type AgentAvailability,
} from "@/types/agent";
import type { CatalogKind } from "@/types/catalog";
import type { BackendKind } from "@/types/storage-backend";
import { cn } from "@/utils";

interface AgentPickerProps {
  value: string | null;
  onChange: (agentId: string) => void;
  workspaceBackend?: BackendKind;
  /**
   * The catalog kinds attached to the workspace; an agent must serve every
   * kind as well as every storage backend.
   */
  workspaceCatalogKinds?: CatalogKind[];
  /**
   * Allow picking a stopped elastic agent. The API starts it for the run: a
   * worksheet run parks queued until it is up, and a schedule restarts it at
   * run time. Static agents stay unselectable when offline either way.
   */
  allowTerminatedElastic?: boolean;
  // The workspace slug, for the "Manage compute" link in the footer.
  ws?: string;
  // Controlled open state, so a caller can open the picker (e.g. "Switch agent").
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

type Group = AgentAvailability["kind"];

const GROUP_ORDER: Group[] = [
  "running",
  "startable",
  "incompatible",
  "unavailable",
];

const GROUP_HEADING: Record<Group, string> = {
  running: "Running",
  startable: "Stopped — starts on run",
  incompatible: "Can't serve this workspace",
  unavailable: "Unavailable",
};

function StatusDot({ agent, kind }: { agent: Agent; kind: Group }) {
  const label =
    kind === "running"
      ? agent.status
      : kind === "startable"
        ? "stopped"
        : kind === "incompatible"
          ? "incompatible"
          : (agent.lifecycle ?? "unavailable");
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        kind === "running" &&
          (agent.status === "degraded"
            ? "bg-[var(--status-running)]"
            : "bg-[var(--status-success)]"),
        kind === "startable" &&
          "border border-[var(--text-tertiary)] bg-transparent",
        kind === "incompatible" && "bg-[var(--text-tertiary)]",
        kind === "unavailable" && "bg-[var(--status-failed)]",
      )}
    />
  );
}

function memoryGb(agent: Agent): number | null {
  return (
    agent.capabilities?.memory_limit_gb ?? agent.requested_memory_gb ?? null
  );
}

function AgentRow({
  agent,
  availability,
  duplicateName,
}: {
  agent: Agent;
  availability: AgentAvailability;
  duplicateName: boolean;
}) {
  const gb = memoryGb(agent);
  const cores = agent.capabilities?.cores ?? agent.requested_cpu;
  const detail = [
    agent.capabilities ? `DuckDB ${agent.capabilities.duckdb_version}` : null,
    cores ? `${cores} cores` : null,
    agent.provider ? "elastic" : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
      <div className="flex min-w-0 items-center gap-2">
        <StatusDot agent={agent} kind={availability.kind} />
        <span className="truncate font-medium text-sm" title={agent.name}>
          {agent.name}
        </span>
        {duplicateName && (
          <span className="shrink-0 font-mono text-2xs text-text-tertiary">
            {agent.capabilities?.host ?? agent.id.slice(0, 8)}
          </span>
        )}
        {gb != null && (
          <span className="ml-auto shrink-0 rounded bg-[var(--bg-elevated)] px-1.5 font-mono text-2xs text-text-secondary">
            {gb} GB
          </span>
        )}
      </div>
      {detail && (
        <span className="truncate pl-4 text-2xs text-text-tertiary">
          {detail}
        </span>
      )}
      {availability.kind === "incompatible" && (
        <span className="pl-4 text-2xs text-[var(--status-failed)]">
          {availability.reason}
        </span>
      )}
    </div>
  );
}

/**
 * The compute "context chip": which agent the next run goes to.
 *
 * Status first, in the manner of a Databricks warehouse menu. Agents that can
 * take a run now come first; stopped elastic agents next (the API starts them);
 * then agents that cannot serve this workspace; the rest — offline, failed,
 * mid-teardown — stay folded away so a long-lived fleet does not bury the one
 * agent that works.
 */
export function AgentPicker({
  value,
  onChange,
  workspaceBackend,
  workspaceCatalogKinds,
  allowTerminatedElastic = false,
  ws,
  open: controlledOpen,
  onOpenChange,
}: AgentPickerProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const open = controlledOpen ?? uncontrolledOpen;
  const setOpen = (next: boolean) => {
    setUncontrolledOpen(next);
    onOpenChange?.(next);
  };
  const [search, setSearch] = useState("");
  const [showUnavailable, setShowUnavailable] = useState(false);
  const [stopping, setStopping] = useState<Agent | null>(null);
  const { data: agents = [] } = useAgents();
  const terminate = useTerminateAgent();
  const restart = useRestartAgent();

  const availability = useMemo(() => {
    const needs = {
      catalogKinds: workspaceCatalogKinds,
      backends: workspaceBackend ? [workspaceBackend] : [],
    };
    return new Map(agents.map((a) => [a.id, agentAvailability(a, needs)]));
  }, [agents, workspaceBackend, workspaceCatalogKinds]);

  const groups = useMemo(() => {
    const byGroup = new Map<Group, Agent[]>(GROUP_ORDER.map((g) => [g, []]));
    for (const agent of agents) {
      byGroup.get(availability.get(agent.id)!.kind)!.push(agent);
    }
    for (const list of byGroup.values()) {
      list.sort((a, b) => a.name.localeCompare(b.name));
    }
    return byGroup;
  }, [agents, availability]);

  const duplicateNames = useMemo(() => {
    const counts = new Map<string, number>();
    for (const a of agents) counts.set(a.name, (counts.get(a.name) ?? 0) + 1);
    return new Set([...counts].filter(([, n]) => n > 1).map(([name]) => name));
  }, [agents]);

  function selectable(agent: Agent): boolean {
    const kind = availability.get(agent.id)?.kind;
    return (
      kind === "running" || (kind === "startable" && allowTerminatedElastic)
    );
  }

  function canControl(agent: Agent): boolean {
    return agentTierAtLeast(agent, "operate") && !!agent.provider;
  }

  async function start(agent: Agent) {
    try {
      await restart.mutateAsync(agent.id);
      toast.success(`Starting ${agent.name}`);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : `Couldn't start ${agent.name}.`,
      );
    }
  }

  async function stop(agent: Agent) {
    try {
      await terminate.mutateAsync(agent.id);
      toast.success(`Stopping ${agent.name}`);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : `Couldn't stop ${agent.name}.`,
      );
    }
    setStopping(null);
  }

  const selected = agents.find((a) => a.id === value);
  const selectedKind = selected ? availability.get(selected.id)?.kind : null;
  const unavailableCount = groups.get("unavailable")!.length;
  const searching = search.trim().length > 0;

  return (
    <>
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) setSearch("");
        }}
      >
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            aria-label="Compute agent"
            className="h-8 max-w-[280px] justify-between gap-1.5 px-2.5 text-sm"
          >
            {selected && selectedKind ? (
              <span className="flex min-w-0 items-center gap-1.5">
                <StatusDot agent={selected} kind={selectedKind} />
                <span className="truncate" title={selected.name}>
                  {selected.name}
                </span>
                {memoryGb(selected) != null && (
                  <span className="shrink-0 font-mono text-2xs text-text-secondary">
                    {memoryGb(selected)} GB
                  </span>
                )}
                {selectedKind === "startable" && (
                  <span className="shrink-0 text-2xs text-text-tertiary">
                    · starts on run
                  </span>
                )}
              </span>
            ) : (
              <span className="text-text-tertiary">Select agent…</span>
            )}
            <ChevronsUpDown className="ml-auto size-3.5 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-[380px] p-0" align="start">
          <Command>
            <CommandInput
              placeholder="Search agents…"
              className="h-9"
              value={search}
              onValueChange={setSearch}
            />
            <CommandList className="max-h-[360px]">
              <CommandEmpty>No agents found.</CommandEmpty>
              {GROUP_ORDER.map((group) => {
                const list = groups.get(group)!;
                if (list.length === 0) return null;
                // Folded away unless asked for, or the search is looking for it.
                if (group === "unavailable" && !showUnavailable && !searching)
                  return null;
                return (
                  <CommandGroup key={group} heading={GROUP_HEADING[group]}>
                    {list.map((agent) => {
                      const avail = availability.get(agent.id)!;
                      const canPick = selectable(agent);
                      const controllable = canControl(agent);
                      return (
                        <CommandItem
                          key={agent.id}
                          value={agent.id}
                          keywords={[
                            agent.name,
                            agent.capabilities?.host ?? "",
                          ]}
                          onSelect={() => {
                            if (!canPick) return;
                            onChange(agent.id);
                            setOpen(false);
                          }}
                          // A row with lifecycle actions stays enabled so its
                          // menu works even when the agent cannot be picked.
                          disabled={!canPick && !controllable}
                          className={cn(
                            "items-start py-2",
                            !canPick && "opacity-70",
                          )}
                        >
                          <AgentRow
                            agent={agent}
                            availability={avail}
                            duplicateName={duplicateNames.has(agent.name)}
                          />
                          {agent.id === value && (
                            <Check
                              className="mt-0.5 size-3.5 shrink-0 text-[var(--brand-slate-blue)]"
                              aria-label="current agent"
                            />
                          )}
                          {controllable && (
                            <AgentActions
                              agent={agent}
                              onStart={() => void start(agent)}
                              onStop={() => setStopping(agent)}
                            />
                          )}
                        </CommandItem>
                      );
                    })}
                  </CommandGroup>
                );
              })}
            </CommandList>
            {unavailableCount > 0 && !searching && (
              <button
                type="button"
                onClick={() => setShowUnavailable((v) => !v)}
                className="border-t border-[var(--border-subtle)] px-3 py-1.5 text-left text-2xs text-text-secondary hover:text-text-primary"
              >
                {showUnavailable
                  ? "Hide unavailable agents"
                  : `Show ${unavailableCount} unavailable`}
              </button>
            )}
            <div className="flex items-center gap-2 border-t border-[var(--border-subtle)] px-3 py-2 text-2xs text-text-secondary">
              {workspaceBackend && (
                <span>
                  Workspace storage: <StorageLabel kind={workspaceBackend} />
                </span>
              )}
              {ws && (
                <Link
                  to="/$ws/compute"
                  params={{ ws }}
                  onClick={() => setOpen(false)}
                  className="ml-auto font-medium text-[var(--brand-slate-blue)] hover:underline"
                >
                  Manage compute ↗
                </Link>
              )}
            </div>
          </Command>
        </PopoverContent>
      </Popover>

      <Dialog
        open={stopping !== null}
        onOpenChange={(v) => !v && setStopping(null)}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Stop {stopping?.name}?</DialogTitle>
            <DialogDescription>
              Queries running on it are cancelled. It starts again the next time
              a run targets it.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setStopping(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={terminate.isPending}
              onClick={() => stopping && void stop(stopping)}
            >
              Stop agent
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function AgentActions({
  agent,
  onStart,
  onStop,
}: {
  agent: Agent;
  onStart: () => void;
  onStop: () => void;
}) {
  const running =
    agent.lifecycle === "running" || agent.lifecycle === "provisioning";
  const stopped =
    agent.lifecycle === "terminated" || agent.lifecycle === "failed";
  if (!running && !stopped) return null;
  // Keep a click on the menu from also selecting the row.
  const stop = (e: React.SyntheticEvent) => e.stopPropagation();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label={`Actions for ${agent.name}`}
          onClick={stop}
          onPointerDown={stop}
          onKeyDown={stop}
          className="shrink-0 rounded p-0.5 text-text-secondary hover:bg-[var(--bg-elevated)] hover:text-text-primary"
        >
          <MoreHorizontal className="size-3.5" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" onClick={stop}>
        {stopped && (
          <DropdownMenuItem onSelect={onStart}>
            <Play className="size-3.5" /> Start
          </DropdownMenuItem>
        )}
        {running && (
          <DropdownMenuItem onSelect={onStop}>
            <Power className="size-3.5" /> Stop…
          </DropdownMenuItem>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
