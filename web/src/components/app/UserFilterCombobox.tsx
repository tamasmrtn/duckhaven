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
import { useAdminUsers } from "@/queries/users";

/**
 * Filters the query history list by user. Admin-only — unlike the agent
 * filter, this reveals who ran a query, so it stays behind the same
 * `queries:admin` permission the server already enforces.
 */
export function UserFilterCombobox({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (userId: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const { data: users = [] } = useAdminUsers();
  const selected = users.find((u) => u.id === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label="filter by user"
          className="h-7 w-[180px] justify-between gap-1 text-xs"
        >
          <span className="truncate">
            {selected ? (selected.name ?? selected.email) : "All users"}
          </span>
          <ChevronsUpDown className="ml-auto size-3.5 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[240px] p-0" align="start">
        <Command>
          <CommandInput placeholder="Search users…" className="h-9" />
          <CommandList>
            <CommandEmpty>No users found.</CommandEmpty>
            <CommandGroup>
              <CommandItem
                value="all users"
                onSelect={() => {
                  onChange(null);
                  setOpen(false);
                }}
              >
                All users
              </CommandItem>
              {users.map((u) => (
                <CommandItem
                  key={u.id}
                  value={u.name || u.email}
                  onSelect={() => {
                    onChange(u.id);
                    setOpen(false);
                  }}
                >
                  {u.name || u.email}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
