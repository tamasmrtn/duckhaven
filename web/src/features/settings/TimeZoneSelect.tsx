import { useMemo, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";
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
  browserZone,
  currentName,
  offsetOf,
  zoneGroups,
  zoneOption,
  zoneScore,
  type ZoneOption,
} from "./timeZones";

/**
 * A searchable list of IANA time zones, grouped by region. A zone is found by
 * its city, its IANA name, its abbreviation or its offset ("+5:30", "UTC-8"),
 * and the browser's own zone is offered first.
 */
export function TimeZoneSelect({
  id,
  value,
  disabled,
  onChange,
}: {
  id: string;
  value: string;
  disabled?: boolean;
  onChange: (zone: string) => void;
}) {
  const [open, setOpen] = useState(false);
  // Offsets move with daylight saving, so they are read again when the list opens.
  const [now, setNow] = useState(() => new Date());
  const groups = useMemo(() => (open ? zoneGroups(now) : []), [open, now]);
  const browser = browserZone();
  const selected = currentName(value);

  function pick(zone: string) {
    setOpen(false);
    if (zone !== value) onChange(zone);
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) setNow(new Date());
        setOpen(next);
      }}
    >
      <PopoverTrigger asChild>
        <Button
          id={id}
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className="h-7 w-full justify-between gap-2 px-2.5 text-sm font-normal"
        >
          <span className="truncate">{value}</span>
          <span className="ml-auto shrink-0 font-mono text-2xs text-text-tertiary">
            {offsetOf(value, now)}
          </span>
          <ChevronsUpDown className="size-3.5 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-[var(--radix-popover-trigger-width)] min-w-[320px] p-0"
        align="start"
      >
        <Command filter={zoneScore}>
          <CommandInput
            placeholder="Search a city, zone or offset…"
            className="h-9"
          />
          <CommandList className="max-h-[320px]">
            <CommandEmpty>No time zone matches.</CommandEmpty>
            {browser && browser !== selected && (
              <CommandGroup heading="Your browser">
                <ZoneItem
                  zone={zoneOption(browser, now)}
                  value={`browser ${browser}`}
                  selected={false}
                  onSelect={pick}
                />
              </CommandGroup>
            )}
            {groups.map(({ region, zones }) => (
              // UTC stands alone: a heading would only repeat it.
              <CommandGroup key={region || "UTC"} heading={region || undefined}>
                {zones.map((zone) => (
                  <ZoneItem
                    key={zone.id}
                    zone={zone}
                    value={zone.id}
                    selected={zone.id === selected}
                    onSelect={pick}
                  />
                ))}
              </CommandGroup>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

function ZoneItem({
  zone,
  value,
  selected,
  onSelect,
}: {
  zone: ZoneOption;
  value: string;
  selected: boolean;
  onSelect: (zone: string) => void;
}) {
  return (
    <CommandItem
      value={value}
      keywords={zone.keywords}
      onSelect={() => onSelect(zone.id)}
      className="gap-2"
    >
      <span className="flex min-w-0 flex-1 items-baseline gap-2">
        <span className="truncate">{zone.city}</span>
        {zone.region && (
          <span className="truncate text-2xs text-text-tertiary">
            {zone.id}
          </span>
        )}
      </span>
      <span className="shrink-0 font-mono text-2xs text-text-secondary">
        {zone.offset}
      </span>
      <Check
        className={
          selected
            ? "size-3.5 shrink-0 text-[var(--brand-slate-blue)]"
            : "invisible size-3.5 shrink-0"
        }
        aria-label={selected ? "current time zone" : undefined}
        aria-hidden={!selected}
      />
    </CommandItem>
  );
}
