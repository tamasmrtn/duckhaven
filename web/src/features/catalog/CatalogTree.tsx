import { useState } from "react";
import { toast } from "sonner";
import {
  ChevronRight,
  ChevronDown,
  Table2,
  Layers,
  Book,
  Lock,
  RefreshCw,
  Plus,
  Link2,
  Info,
  Shield,
} from "lucide-react";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useSchemas, useTable, useTables } from "@/queries/schemas";
import { useRefreshCatalogStats } from "@/queries/schemas.mutations";
import {
  useCatalogs,
  useDetachCatalog,
  useDropCatalog,
} from "@/queries/catalogs";
import { CatalogNodeMenu } from "@/features/catalog/CatalogNodeMenu";
import { CreateSchemaDialog } from "@/features/catalog/CreateSchemaDialog";
import { PermissionsDialog } from "@/features/catalog/PermissionsDialog";
import {
  AttachCatalogDialog,
  CreateCatalogDialog,
} from "@/features/catalog/CatalogDialogs";
import {
  CatalogInfoDialog,
  backendLabel,
} from "@/features/catalog/CatalogInfoDialog";
import { StorageIcon } from "@/components/app/StorageIcon";
import type { BackendKind } from "@/types/storage-backend";
import { cn, formatRowCount } from "@/utils";
import type { Catalog, CatalogTable } from "@/types/catalog";

interface TableNodeProps {
  ws: string;
  catalog: string;
  schemaName: string;
  table: CatalogTable;
  onTableClick: (catalog: string, schema: string, table: string) => void;
}

function TableNode({
  ws,
  catalog,
  schemaName,
  table,
  onTableClick,
}: TableNodeProps) {
  const [open, setOpen] = useState(false);
  // Columns aren't in the table-list payload (Polaris lists identifiers only),
  // so fetch the table detail lazily on expand — sharing the detail view's cache.
  const { data, isLoading } = useTable(
    ws,
    catalog,
    schemaName,
    open ? table.name : "",
  );
  const columns = data?.columns ?? [];

  return (
    <div>
      <CatalogNodeMenu
        ws={ws}
        catalog={catalog}
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
            onClick={() => onTableClick(catalog, schemaName, table.name)}
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
  catalog: string;
  schemaName: string;
  filter: string;
  onTableClick: (catalog: string, schema: string, table: string) => void;
  onSchemaClick?: (catalog: string, schema: string) => void;
}

function SchemaNode({
  ws,
  catalog,
  schemaName,
  filter,
  onTableClick,
  onSchemaClick,
}: SchemaNodeProps) {
  const [open, setOpen] = useState(true);
  const { data: tables, isLoading } = useTables(
    ws,
    catalog,
    open ? schemaName : "",
  );

  const filtered = (tables ?? []).filter(
    (t) => !filter || t.name.toLowerCase().includes(filter.toLowerCase()),
  );

  if (filter && filtered.length === 0) return null;

  return (
    <div>
      <CatalogNodeMenu
        ws={ws}
        catalog={catalog}
        node={{ kind: "schema", schema: schemaName }}
      >
        <div className="flex w-full items-center rounded text-sm font-medium text-text-primary hover:bg-accent">
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="shrink-0 rounded p-1 text-text-secondary hover:text-text-primary focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
            aria-expanded={open}
            aria-label={open ? "Collapse schema" : "Expand schema"}
          >
            {open ? (
              <ChevronDown className="size-3.5 shrink-0" />
            ) : (
              <ChevronRight className="size-3.5 shrink-0" />
            )}
          </button>
          <button
            type="button"
            onClick={() =>
              onSchemaClick
                ? onSchemaClick(catalog, schemaName)
                : setOpen((v) => !v)
            }
            className="flex min-w-0 flex-1 items-center gap-1.5 rounded py-1 pr-2 focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
          >
            <Layers className="size-3.5 shrink-0 text-[var(--brand-maya-blue)]" />
            <span className="truncate">{schemaName}</span>
          </button>
        </div>
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
                  catalog={catalog}
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

// The built-in, read-only metadata schema every catalog exposes. It is not a
// Polaris namespace (so it never comes back from `useSchemas`); it is DuckDB's
// native, live `information_schema`, surfaced here as a virtual node. The views
// listed are the ones DuckHaven supports — see docs/reference/sql-support.md.
const INFORMATION_SCHEMA = "information_schema";
// `columns` is deliberately absent: DuckDB cannot introspect the columns of an
// attached Iceberg relation through it — it returns an `UNKNOWN` placeholder —
// so offering it here would seed a query that looks broken. Column detail comes
// from `DESCRIBE`, or from clicking a table (which reads Polaris directly).
const INFORMATION_SCHEMA_VIEWS = ["schemata", "tables", "views"] as const;

interface InformationSchemaNodeProps {
  catalog: string;
  filter: string;
  onMetaViewClick?: (catalog: string, view: string) => void;
}

// Virtual, read-only `information_schema` node. Always present (never created),
// never writable — hence the lock icon and the "read-only" badge, and no
// create/drop affordances. Clicking a view seeds a scoped query when a handler
// is provided (worksheet sidebar); elsewhere the views are display-only.
function InformationSchemaNode({
  catalog,
  filter,
  onMetaViewClick,
}: InformationSchemaNodeProps) {
  const [open, setOpen] = useState(false);
  const f = filter.toLowerCase();
  const views = INFORMATION_SCHEMA_VIEWS.filter((v) => !f || v.includes(f));

  // Hide the node entirely when a table search matches none of its views.
  if (filter && views.length === 0 && !INFORMATION_SCHEMA.includes(f))
    return null;

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-sm font-medium text-text-primary hover:bg-accent focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
        aria-expanded={open}
        title="Built-in, read-only metadata schema"
      >
        {open ? (
          <ChevronDown className="size-3.5 shrink-0 text-text-secondary" />
        ) : (
          <ChevronRight className="size-3.5 shrink-0 text-text-secondary" />
        )}
        <Layers className="size-3.5 shrink-0 text-[var(--brand-maya-blue)]" />
        <span className="truncate">{INFORMATION_SCHEMA}</span>
        <span className="ml-1 flex items-center gap-0.5 rounded bg-accent px-1 text-2xs text-text-tertiary">
          <Lock className="size-2.5" />
          read-only
        </span>
      </button>

      {open && (
        <div className="ml-3 border-l border-[var(--border-subtle)] pl-2">
          {views.map((view) => (
            <button
              key={view}
              type="button"
              disabled={!onMetaViewClick}
              onClick={() => onMetaViewClick?.(catalog, view)}
              className="flex w-full items-center gap-1.5 rounded py-1 pr-1.5 pl-1.5 text-sm text-text-secondary hover:bg-accent hover:text-text-primary focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)] disabled:cursor-default disabled:hover:bg-transparent"
            >
              <Table2 className="size-3.5 shrink-0 text-text-tertiary" />
              <span className="truncate">{view}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

interface CatalogNodeProps {
  ws: string;
  catalog: Catalog;
  filter: string;
  defaultOpen: boolean;
  onTableClick: (catalog: string, schema: string, table: string) => void;
  onMetaViewClick?: (catalog: string, view: string) => void;
  onCatalogClick?: (catalog: string) => void;
  onSchemaClick?: (catalog: string, schema: string) => void;
}

function CatalogNode({
  ws,
  catalog,
  filter,
  defaultOpen,
  onTableClick,
  onMetaViewClick,
  onCatalogClick,
  onSchemaClick,
}: CatalogNodeProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [createSchemaOpen, setCreateSchemaOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [permsOpen, setPermsOpen] = useState(false);
  const { data: schemas, isLoading } = useSchemas(ws, open ? catalog.slug : "");
  const detach = useDetachCatalog(ws);
  const drop = useDropCatalog(ws);
  // A catalog attached to more than this workspace cannot be dropped from here.
  const shared = (catalog.attached_workspaces ?? 1) > 1;

  async function handleDetach() {
    try {
      await detach.mutateAsync(catalog.slug);
      toast.success(`Detached ${catalog.slug}`);
    } catch {
      toast.error(`Couldn't detach ${catalog.slug}`);
    }
  }

  async function handleDrop() {
    try {
      await drop.mutateAsync(catalog.id);
      toast.success(`Dropped catalog ${catalog.slug}`);
    } catch {
      toast.error(
        `Couldn't drop ${catalog.slug} — detach it everywhere first.`,
      );
    }
  }

  return (
    <div>
      <ContextMenu>
        <ContextMenuTrigger asChild>
          <div className="flex w-full items-center rounded text-sm font-semibold text-text-primary hover:bg-accent">
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="shrink-0 rounded p-1 text-text-secondary hover:text-text-primary focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
              aria-expanded={open}
              aria-label={open ? "Collapse catalog" : "Expand catalog"}
            >
              {open ? (
                <ChevronDown className="size-3.5 shrink-0" />
              ) : (
                <ChevronRight className="size-3.5 shrink-0" />
              )}
            </button>
            <button
              type="button"
              onClick={() =>
                onCatalogClick
                  ? onCatalogClick(catalog.slug)
                  : setOpen((v) => !v)
              }
              className="flex min-w-0 flex-1 items-center gap-1.5 rounded py-1 pr-2 focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
            >
              <Book className="size-3.5 shrink-0 text-[var(--brand-slate-blue)]" />
              <span className="truncate">{catalog.slug}</span>
              {/* Storage-backend indicator: which object store this catalog lives on. */}
              <span
                className="ml-1 inline-flex shrink-0"
                title={`Storage: ${backendLabel(catalog.storage_backend_kind)}`}
              >
                <StorageIcon
                  kind={catalog.storage_backend_kind as BackendKind}
                  className="size-3 text-text-tertiary"
                />
              </span>
              {catalog.access_mode === "scoped" && (
                <span className="ml-1 rounded bg-accent px-1 text-2xs text-text-tertiary">
                  scoped
                </span>
              )}
              {catalog.is_default && (
                <span className="ml-1 rounded bg-accent px-1 text-2xs text-text-tertiary">
                  default
                </span>
              )}
            </button>
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent>
          <ContextMenuItem onSelect={() => setInfoOpen(true)}>
            <Info />
            Catalog information
          </ContextMenuItem>
          <ContextMenuSeparator />
          <ContextMenuItem onSelect={() => setCreateSchemaOpen(true)}>
            <Plus />
            Create schema
          </ContextMenuItem>
          <ContextMenuItem onSelect={() => setPermsOpen(true)}>
            <Shield />
            Permissions…
          </ContextMenuItem>
          <ContextMenuSeparator />
          <ContextMenuItem onSelect={handleDetach}>
            <Link2 />
            Detach from workspace
          </ContextMenuItem>
          <ContextMenuItem destructive disabled={shared} onSelect={handleDrop}>
            <Table2 />
            {shared ? "Drop (shared — detach first)" : "Drop catalog"}
          </ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>

      {open && (
        <div className="ml-3 border-l border-[var(--border-subtle)] pl-2">
          {isLoading ? (
            Array.from({ length: 2 }).map((_, i) => (
              <Skeleton
                key={i}
                className="my-1 h-5 w-full animate-shimmer rounded"
              />
            ))
          ) : (
            <>
              {schemas?.length === 0 && !filter && (
                <p className="px-2 py-1 text-2xs text-text-tertiary">
                  No user schemas.
                </p>
              )}
              {schemas?.map((s) => (
                <SchemaNode
                  key={s.name}
                  ws={ws}
                  catalog={catalog.slug}
                  schemaName={s.name}
                  filter={filter}
                  onTableClick={onTableClick}
                  onSchemaClick={onSchemaClick}
                />
              ))}
              {/* Read-only metadata schema (virtual). Hidden for a scoped
                  catalog: DuckDB computes these views across every attachment
                  and cannot filter them by grant, so querying them is rejected
                  there — offering the node would only lead to an error. The
                  tree itself is the grant-filtered way to browse. */}
              {catalog.access_mode !== "scoped" && (
                <InformationSchemaNode
                  catalog={catalog.slug}
                  filter={filter}
                  onMetaViewClick={onMetaViewClick}
                />
              )}
            </>
          )}
        </div>
      )}

      <CreateSchemaDialog
        ws={ws}
        catalog={catalog.slug}
        open={createSchemaOpen}
        onOpenChange={setCreateSchemaOpen}
      />
      <CatalogInfoDialog
        catalog={catalog}
        open={infoOpen}
        onOpenChange={setInfoOpen}
      />
      <PermissionsDialog
        ws={ws}
        catalog={catalog.slug}
        open={permsOpen}
        onOpenChange={setPermsOpen}
      />
    </div>
  );
}

interface CatalogTreeProps {
  ws: string;
  workspaceName: string;
  onTableClick: (catalog: string, schema: string, table: string) => void;
  onMetaViewClick?: (catalog: string, view: string) => void;
  // When provided, clicking a catalog / schema label navigates to its detail
  // pane (catalog page); when omitted the label just expands the node
  // (worksheet sidebar).
  onCatalogClick?: (catalog: string) => void;
  onSchemaClick?: (catalog: string, schema: string) => void;
}

export function CatalogTree({
  ws,
  workspaceName,
  onTableClick,
  onMetaViewClick,
  onCatalogClick,
  onSchemaClick,
}: CatalogTreeProps) {
  const [filter, setFilter] = useState("");
  const [createCatalogOpen, setCreateCatalogOpen] = useState(false);
  const [createSchemaOpen, setCreateSchemaOpen] = useState(false);
  const [attachCatalogOpen, setAttachCatalogOpen] = useState(false);
  const { data: catalogs, isLoading } = useCatalogs(ws);
  const refreshStats = useRefreshCatalogStats(ws);

  // Probe row counts for any tables that lack one (workspace-wide via the
  // default-catalog shim), then re-read the tree on settle.
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
          <span className="truncate text-xs font-semibold text-text-secondary uppercase tracking-wide">
            {workspaceName}
          </span>
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
              onClick={() => setAttachCatalogOpen(true)}
              title="Attach catalog"
              aria-label="Attach catalog"
              className="rounded p-1 text-text-secondary hover:bg-accent hover:text-text-primary focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
            >
              <Link2 className="size-3.5" />
            </button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  title="Create"
                  aria-label="Create"
                  className="rounded p-1 text-text-secondary hover:bg-accent hover:text-text-primary focus-visible:outline-2 focus-visible:outline-[var(--brand-slate-blue)]"
                >
                  <Plus className="size-3.5" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => setCreateCatalogOpen(true)}>
                  <Book className="size-3.5" />
                  Create catalog
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={() => setCreateSchemaOpen(true)}>
                  <Layers className="size-3.5" />
                  Create schema
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
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
        ) : catalogs?.length === 0 ? (
          <p className="px-2 py-3 text-sm text-text-tertiary">
            No catalogs attached.
          </p>
        ) : (
          <div className="space-y-0.5">
            {catalogs?.map((c, i) => (
              <CatalogNode
                key={c.id}
                ws={ws}
                catalog={c}
                filter={filter}
                defaultOpen={c.is_default || (catalogs.length === 1 && i === 0)}
                onTableClick={onTableClick}
                onMetaViewClick={onMetaViewClick}
                onCatalogClick={onCatalogClick}
                onSchemaClick={onSchemaClick}
              />
            ))}
          </div>
        )}
      </div>

      <CreateCatalogDialog
        ws={ws}
        open={createCatalogOpen}
        onOpenChange={setCreateCatalogOpen}
      />
      <CreateSchemaDialog
        ws={ws}
        allowCatalogChoice
        open={createSchemaOpen}
        onOpenChange={setCreateSchemaOpen}
      />
      <AttachCatalogDialog
        ws={ws}
        attachedSlugs={(catalogs ?? []).map((c) => c.slug)}
        open={attachCatalogOpen}
        onOpenChange={setAttachCatalogOpen}
      />
    </div>
  );
}
