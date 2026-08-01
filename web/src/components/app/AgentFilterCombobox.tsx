import { useState } from "react";
import { ChevronsUpDown } from "lucide-react";
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
import { useAgents } from "@/queries/agents";

/**
 * Filters the query history list by agent. Open to any workspace member — an
 * agent's identity isn't sensitive, unlike who ran a query.
 */
export function AgentFilterCombobox({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (agentId: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const { data: agents = [] } = useAgents();
  const selected = agents.find((a) => a.id === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label="filter by agent"
          className="h-7 w-[180px] justify-between gap-1 text-xs"
        >
          <span className="truncate">
            {selected ? selected.name : "All agents"}
          </span>
          <ChevronsUpDown className="ml-auto size-3.5 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[240px] p-0" align="start">
        <Command>
          <CommandInput placeholder="Search agents…" className="h-9" />
          <CommandList>
            <CommandEmpty>No agents found.</CommandEmpty>
            <CommandGroup>
              <CommandItem
                value="all agents"
                onSelect={() => {
                  onChange(null);
                  setOpen(false);
                }}
              >
                All agents
              </CommandItem>
              {agents.map((agent) => (
                <CommandItem
                  key={agent.id}
                  value={agent.name}
                  onSelect={() => {
                    onChange(agent.id);
                    setOpen(false);
                  }}
                >
                  {agent.name}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
