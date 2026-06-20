import { useState } from "react";
import { ChevronRight, ChevronDown, Table2, Layers } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useSchemas, useTables } from "@/queries/schemas";
import { CatalogNodeMenu } from "@/features/catalog/CatalogNodeMenu";

function formatRowCount(n: number | null) {
  if (n == null) return "";
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

interface SchemaNodeProps {
  ws: string;
  schemaName: string;
  filter: string;
  onTableClick: (schema: string, table: string) => void;
}

function SchemaNode({ ws, schemaName, filter, onTableClick }: SchemaNodeProps) {
  const [open, setOpen] = useState(true);
  const { data: tables, isLoading } = useTables(ws, open ? schemaName : "");

  const filtered = (tables ?? []).filter(
    (t) => !filter || t.name.toLowerCase().includes(filter.toLowerCase()),
  );

  if (filter && filtered.length === 0) return null;

  return (
    <div>
      <CatalogNodeMenu ws={ws} node={{ kind: "schema", schema: schemaName }}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-sm font-medium text-text-primary hover:bg-accent focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
          aria-expanded={open}
        >
          {open ? (
            <ChevronDown className="size-3.5 shrink-0 text-text-secondary" />
          ) : (
            <ChevronRight className="size-3.5 shrink-0 text-text-secondary" />
          )}
          <Layers className="size-3.5 shrink-0 text-[var(--brand-maya-blue)]" />
          <span className="truncate">{schemaName}</span>
        </button>
      </CatalogNodeMenu>

      {open && (
        <div className="ml-3 border-l border-[var(--border-subtle)] pl-2">
          {isLoading
            ? Array.from({ length: 2 }).map((_, i) => (
                <Skeleton
                  key={i}
                  className="my-1 h-5 w-full animate-shimmer rounded"
                />
              ))
            : filtered.map((table) => (
                <CatalogNodeMenu
                  key={table.name}
                  ws={ws}
                  node={{
                    kind: "table",
                    schema: schemaName,
                    table: table.name,
                  }}
                >
                  <button
                    type="button"
                    onClick={() => onTableClick(schemaName, table.name)}
                    className="flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-sm text-text-secondary hover:bg-accent hover:text-text-primary focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
                  >
                    <Table2 className="size-3.5 shrink-0 text-text-tertiary" />
                    <span className="truncate">{table.name}</span>
                    {table.row_count != null && (
                      <span className="ml-auto font-mono text-2xs text-text-tertiary font-tabular">
                        {formatRowCount(table.row_count)}
                      </span>
                    )}
                  </button>
                </CatalogNodeMenu>
              ))}
        </div>
      )}
    </div>
  );
}

interface CatalogTreeProps {
  ws: string;
  workspaceName: string;
  onTableClick: (schema: string, table: string) => void;
}

export function CatalogTree({
  ws,
  workspaceName,
  onTableClick,
}: CatalogTreeProps) {
  const [filter, setFilter] = useState("");
  const { data: schemas, isLoading } = useSchemas(ws);

  return (
    <div className="flex h-full flex-col gap-2 p-2">
      <Input
        placeholder="Search tables…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        className="h-8 text-sm"
        aria-label="Search tables"
      />

      <div className="flex-1 overflow-auto">
        <CatalogNodeMenu ws={ws} node={{ kind: "catalog" }}>
          <div className="mb-1 px-2 py-1">
            <span className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
              {workspaceName}
            </span>
          </div>
        </CatalogNodeMenu>

        {isLoading ? (
          <div className="space-y-1 px-1">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton
                key={i}
                className="h-6 w-full animate-shimmer rounded"
              />
            ))}
          </div>
        ) : schemas?.length === 0 ? (
          <p className="px-2 py-3 text-sm text-text-tertiary">
            No schemas yet.
          </p>
        ) : (
          <div className="space-y-0.5">
            {schemas?.map((s) => (
              <SchemaNode
                key={s.name}
                ws={ws}
                schemaName={s.name}
                filter={filter}
                onTableClick={onTableClick}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
