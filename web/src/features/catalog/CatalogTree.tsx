import { useState } from "react";
import { toast } from "sonner";
import {
  ChevronRight,
  ChevronDown,
  Table2,
  Layers,
  RefreshCw,
  Plus,
} from "lucide-react";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useSchemas, useTable, useTables } from "@/queries/schemas";
import { useRefreshCatalogStats } from "@/queries/schemas.mutations";
import { CatalogNodeMenu } from "@/features/catalog/CatalogNodeMenu";
import { CreateSchemaDialog } from "@/features/catalog/CreateSchemaDialog";
import { cn } from "@/utils";
import type { CatalogTable } from "@/types/catalog";

function formatRowCount(n: number | null) {
  if (n == null) return "";
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

interface TableNodeProps {
  ws: string;
  schemaName: string;
  table: CatalogTable;
  onTableClick: (schema: string, table: string) => void;
}

function TableNode({ ws, schemaName, table, onTableClick }: TableNodeProps) {
  const [open, setOpen] = useState(false);
  // Columns aren't in the table-list payload (Polaris lists identifiers only),
  // so fetch the table detail lazily on expand — sharing the detail view's cache.
  const { data, isLoading } = useTable(ws, schemaName, open ? table.name : "");
  const columns = data?.columns ?? [];

  return (
    <div>
      <CatalogNodeMenu
        ws={ws}
        node={{ kind: "table", schema: schemaName, table: table.name }}
      >
        <div className="flex w-full items-center rounded text-text-secondary hover:bg-accent hover:text-text-primary">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="shrink-0 rounded p-1 text-text-tertiary hover:text-text-primary focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
            aria-expanded={open}
            aria-label={open ? "Hide columns" : "Show columns"}
          >
            {open ? (
              <ChevronDown className="size-3" />
            ) : (
              <ChevronRight className="size-3" />
            )}
          </button>
          <button
            type="button"
            onClick={() => onTableClick(schemaName, table.name)}
            className="flex min-w-0 flex-1 items-center gap-1.5 rounded py-1 pr-1.5 text-sm focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
          >
            <Table2 className="size-3.5 shrink-0 text-text-tertiary" />
            <span className="truncate">{table.name}</span>
            {table.row_count != null && (
              <span className="ml-auto font-mono text-2xs text-text-tertiary font-tabular">
                {formatRowCount(table.row_count)}
              </span>
            )}
          </button>
        </div>
      </CatalogNodeMenu>

      {open && (
        <div className="ml-4 border-l border-[var(--border-subtle)] pl-2">
          {isLoading ? (
            Array.from({ length: 3 }).map((_, i) => (
              <Skeleton
                key={i}
                className="my-1 h-4 w-full animate-shimmer rounded"
              />
            ))
          ) : columns.length === 0 ? (
            <p className="px-1.5 py-1 text-2xs text-text-tertiary">
              No columns.
            </p>
          ) : (
            columns.map((col) => (
              <div
                key={col.name}
                className="flex items-center gap-1.5 px-1.5 py-0.5 text-xs"
                title={`${col.name} ${col.type}${col.nullable ? "" : " · not null"}`}
              >
                <span className="truncate font-mono text-text-secondary">
                  {col.name}
                </span>
                <span className="ml-auto shrink-0 font-mono text-2xs text-[var(--brand-maya-blue)]">
                  {col.type}
                </span>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
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
                <TableNode
                  key={table.name}
                  ws={ws}
                  schemaName={schemaName}
                  table={table}
                  onTableClick={onTableClick}
                />
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
  const [createSchemaOpen, setCreateSchemaOpen] = useState(false);
  const { data: schemas, isLoading } = useSchemas(ws);
  const refreshStats = useRefreshCatalogStats(ws);

  // Probe row counts for any tables that lack one (e.g. created from the
  // worksheet), then re-read the tree (handled by the mutation's onSettled).
  async function handleRefresh() {
    try {
      await refreshStats.mutateAsync();
    } catch {
      toast.error("Couldn't refresh row counts — no agent connected.");
    }
  }

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
        <div className="mb-1 flex items-center justify-between gap-1 px-2 py-1">
          <CatalogNodeMenu ws={ws} node={{ kind: "catalog" }}>
            <span className="truncate text-xs font-semibold text-text-secondary uppercase tracking-wide">
              {workspaceName}
            </span>
          </CatalogNodeMenu>
          <div className="flex shrink-0 items-center gap-0.5">
            <button
              type="button"
              onClick={handleRefresh}
              disabled={refreshStats.isPending}
              title="Refresh catalog"
              aria-label="Refresh catalog"
              className="rounded p-1 text-text-secondary hover:bg-accent hover:text-text-primary focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
            >
              <RefreshCw
                className={cn(
                  "size-3.5",
                  refreshStats.isPending && "animate-spin",
                )}
              />
            </button>
            <button
              type="button"
              onClick={() => setCreateSchemaOpen(true)}
              title="Add schema"
              aria-label="Add schema"
              className="rounded p-1 text-text-secondary hover:bg-accent hover:text-text-primary focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
            >
              <Plus className="size-3.5" />
            </button>
          </div>
        </div>

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

      <CreateSchemaDialog
        ws={ws}
        open={createSchemaOpen}
        onOpenChange={setCreateSchemaOpen}
      />
    </div>
  );
}
