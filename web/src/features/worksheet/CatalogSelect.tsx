import { useState } from "react";
import { Check, ChevronsUpDown, Library } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Command,
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
import type { Catalog } from "@/types/catalog";

/**
 * The worksheet's active catalog, USEd for unqualified table names. Styled as
 * a context chip beside the compute agent, in the manner of the
 * catalog.schema chip in the Databricks editor.
 */
export function CatalogSelect({
  catalogs,
  value,
  onChange,
}: {
  catalogs: Catalog[];
  value: string | undefined;
  onChange: (slug: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const current = catalogs.find((c) => c.slug === value);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label="Active catalog"
          title="Active catalog (USEd for unqualified table names)"
          className="h-8 max-w-[220px] gap-1.5 px-2.5 text-sm"
        >
          <Library className="size-3.5 shrink-0 text-text-secondary" />
          <span className="truncate">{current?.slug ?? "Catalog"}</span>
          {current?.is_default && (
            <span className="shrink-0 rounded bg-accent px-1 text-2xs text-text-secondary">
              default
            </span>
          )}
          <ChevronsUpDown className="size-3.5 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-64 p-0" align="start">
        <Command>
          {catalogs.length > 6 && (
            <CommandInput placeholder="Search catalogs…" className="h-9" />
          )}
          <CommandList>
            <CommandGroup>
              {catalogs.map((c) => (
                <CommandItem
                  key={c.id}
                  value={c.slug}
                  onSelect={() => {
                    onChange(c.slug);
                    setOpen(false);
                  }}
                >
                  <span className="truncate">{c.slug}</span>
                  {c.is_default && (
                    <span className="text-2xs text-text-tertiary">default</span>
                  )}
                  {c.slug === value && <Check className="ml-auto size-3.5" />}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
