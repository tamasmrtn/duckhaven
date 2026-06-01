import { useState } from "react";
import { useParams, Link, useNavigate } from "@tanstack/react-router";
import { ChevronRight, Pencil, ExternalLink, Plus, Table2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/app/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useSchemas,
  useTable,
  useTableSample,
  useTables,
} from "@/queries/schemas";
import { useDeleteTable } from "@/queries/schemas.mutations";
import { ResultsTable } from "@/features/worksheet/ResultsTable";
import { StorageIcon } from "@/components/app/StorageIcon";
import { useWorkspace } from "@/queries/workspaces";
import { CreateSchemaDialog } from "@/features/catalog/CreateSchemaDialog";
import { CreateTableDialog } from "@/features/catalog/CreateTableDialog";
import { cn } from "@/utils";

function formatBytes(n: number | null) {
  if (n == null) return "—";
  if (n >= 1_073_741_824) return `${(n / 1_073_741_824).toFixed(1)} GB`;
  if (n >= 1_048_576) return `${(n / 1_048_576).toFixed(1)} MB`;
  return `${(n / 1024).toFixed(0)} KB`;
}

function formatNumber(n: number | null) {
  if (n == null) return "—";
  return n.toLocaleString();
}

function SchemaList({ ws }: { ws: string }) {
  const { data: schemas = [], isLoading } = useSchemas(ws);
  const { data: workspace } = useWorkspace(ws);
  const [selectedSchema, setSelectedSchema] = useState<string | null>(null);
  const { data: tables = [] } = useTables(ws, selectedSchema ?? "");
  const [schemaDialogOpen, setSchemaDialogOpen] = useState(false);
  const [tableDialogOpen, setTableDialogOpen] = useState(false);

  return (
    <div className="flex h-full gap-0">
      {/* Schema list */}
      <div className="w-56 shrink-0 border-r border-[var(--border-subtle)] bg-[var(--bg-surface)] p-2">
        <div className="mb-2 flex items-center justify-between px-2">
          <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
            {workspace?.name ?? ws}
          </p>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label="new schema"
            className="h-5 w-5 p-0"
            onClick={() => setSchemaDialogOpen(true)}
          >
            <Plus className="size-3" />
          </Button>
        </div>
        <CreateSchemaDialog
          ws={ws}
          open={schemaDialogOpen}
          onOpenChange={setSchemaDialogOpen}
        />
        {isLoading ? (
          <div className="space-y-1">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton
                key={i}
                className="h-7 w-full animate-shimmer rounded"
              />
            ))}
          </div>
        ) : schemas.length === 0 ? (
          <p className="px-2 text-sm text-text-tertiary">No schemas.</p>
        ) : (
          schemas.map((s) => (
            <button
              key={s.name}
              type="button"
              onClick={() => setSelectedSchema(s.name)}
              className={cn(
                "flex w-full items-center gap-2 rounded px-2 py-1.5 text-sm",
                selectedSchema === s.name
                  ? "bg-accent text-text-primary"
                  : "text-text-secondary hover:bg-accent/50",
              )}
            >
              <ChevronRight className="size-3.5" />
              {s.name}
            </button>
          ))
        )}
      </div>

      {/* Table list */}
      {selectedSchema && (
        <div className="w-56 shrink-0 border-r border-[var(--border-subtle)] bg-[var(--bg-surface)] p-2">
          <div className="mb-2 flex items-center justify-between px-2">
            <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
              {selectedSchema}
            </p>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              aria-label="new table"
              className="h-5 w-5 p-0"
              onClick={() => setTableDialogOpen(true)}
            >
              <Plus className="size-3" />
            </Button>
          </div>
          <CreateTableDialog
            ws={ws}
            schema={selectedSchema}
            open={tableDialogOpen}
            onOpenChange={setTableDialogOpen}
          />
          {tables.length === 0 ? (
            <EmptyState icon={Table2} title="No tables in this schema" />
          ) : (
            tables.map((t) => (
              <Link
                key={t.name}
                to="/$ws/catalog/$schema/$table"
                params={{ ws, schema: selectedSchema, table: t.name }}
                className="flex w-full items-center justify-between rounded px-2 py-1.5 text-sm text-text-secondary hover:bg-accent/50 hover:text-text-primary"
              >
                {t.name}
                <span className="font-mono text-2xs text-text-tertiary font-tabular">
                  {t.row_count != null
                    ? (t.row_count / 1_000_000).toFixed(1) + "M"
                    : ""}
                </span>
              </Link>
            ))
          )}
        </div>
      )}

      {/* Placeholder */}
      {!selectedSchema && (
        <div className="flex flex-1 items-center justify-center text-sm text-text-tertiary">
          Select a schema to browse tables.
        </div>
      )}
    </div>
  );
}

function TableDetail({
  ws,
  schema,
  table,
}: {
  ws: string;
  schema: string;
  table: string;
}) {
  const { data: tableData, isLoading } = useTable(ws, schema, table);
  const { data: sampleData, isLoading: sampleLoading } = useTableSample(
    ws,
    schema,
    table,
  );
  const { data: workspace } = useWorkspace(ws);
  const navigate = useNavigate();
  const deleteTable = useDeleteTable(ws, schema);
  const [dropOpen, setDropOpen] = useState(false);

  async function handleDrop() {
    await deleteTable.mutateAsync(table);
    toast.success(`Dropped ${schema}.${table}`);
    setDropOpen(false);
    navigate({ to: "/$ws/catalog", params: { ws } });
  }

  if (isLoading) {
    return (
      <div className="flex h-full flex-col gap-4 p-6">
        <Skeleton className="h-8 w-64 animate-shimmer rounded" />
        <Skeleton className="h-24 w-full animate-shimmer rounded" />
      </div>
    );
  }

  if (!tableData)
    return <div className="p-6 text-text-tertiary">Table not found.</div>;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <div className="flex items-center gap-2 text-xs text-text-secondary">
          <span className="font-medium text-text-primary">{ws}</span>
          <ChevronRight className="size-3" />
          <span>{schema}</span>
          <ChevronRight className="size-3" />
          <span className="font-medium text-text-primary">{table}</span>
        </div>

        <div className="mt-3 flex items-start justify-between">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              {workspace && (
                <StorageIcon
                  kind={workspace.storage_backend_kind}
                  className="text-text-secondary"
                />
              )}
              <span className="text-xs text-text-secondary">
                {tableData.format} · Catalog Commits{" "}
                {tableData.catalog_commits ? "ON" : "OFF"} ·{" "}
                {formatNumber(tableData.row_count)} rows ·{" "}
                {formatBytes(tableData.size_bytes)}
              </span>
            </div>
            {tableData.last_write_at && (
              <p className="text-xs text-text-tertiary">
                Owner:{" "}
                <span className="text-text-secondary">{tableData.owner}</span> ·
                Last write: {new Date(tableData.last_write_at).toLocaleString()}{" "}
                by {tableData.last_write_by} ({tableData.last_write_agent})
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-7 gap-1.5 text-xs"
              onClick={() => setDropOpen(true)}
            >
              <Pencil className="size-3" />
              Rename / Drop
            </Button>
            <Button variant="outline" size="sm" className="h-7 gap-1.5 text-xs">
              <ExternalLink className="size-3" />
              Query this table
            </Button>
          </div>
        </div>
      </div>

      <div className="flex flex-1 gap-0 overflow-hidden">
        {/* Schema column */}
        <div className="w-80 shrink-0 border-r border-[var(--border-subtle)] overflow-auto">
          <div className="px-4 py-3">
            <p className="mb-2 text-xs font-semibold text-text-secondary uppercase tracking-wide">
              Schema
            </p>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--border-subtle)]">
                  <th className="py-1 text-left text-xs font-medium text-text-secondary">
                    #
                  </th>
                  <th className="py-1 text-left text-xs font-medium text-text-secondary">
                    Column
                  </th>
                  <th className="py-1 text-left text-xs font-medium text-text-secondary">
                    Type
                  </th>
                  <th className="py-1 text-left text-xs font-medium text-text-secondary">
                    Null?
                  </th>
                </tr>
              </thead>
              <tbody>
                {tableData.columns.map((col) => (
                  <tr
                    key={col.name}
                    className="border-b border-[var(--border-subtle)]"
                  >
                    <td className="py-1.5 pr-2 font-mono text-xs text-text-tertiary font-tabular">
                      {col.position}
                    </td>
                    <td className="py-1.5 pr-2 font-mono text-xs text-text-primary">
                      {col.name}
                    </td>
                    <td className="py-1.5 pr-2 text-xs text-[var(--brand-maya-blue)]">
                      {col.type}
                    </td>
                    <td className="py-1.5 text-xs text-text-tertiary">
                      {col.nullable ? "Y" : "N"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Sample rows */}
        <div className="flex-1 overflow-hidden flex flex-col">
          <div className="border-b border-[var(--border-subtle)] px-4 py-2 shrink-0">
            <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
              Sample (LIMIT 20)
            </p>
          </div>
          <div className="flex-1 overflow-hidden">
            <ResultsTable
              columns={sampleData?.columns ?? []}
              rows={sampleData?.rows ?? []}
              total={sampleData?.total ?? 0}
              isLoading={sampleLoading}
            />
          </div>
        </div>
      </div>

      <Dialog open={dropOpen} onOpenChange={setDropOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Manage {table}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled
                      className="text-xs"
                    >
                      Rename
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  Not supported by the DuckDB UC extension yet.
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
            <p className="text-text-secondary">
              Dropping permanently removes{" "}
              <span className="font-mono">
                {schema}.{table}
              </span>{" "}
              from the Polaris catalog. This cannot be undone.
            </p>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setDropOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={handleDrop}
              disabled={deleteTable.isPending}
            >
              {deleteTable.isPending ? "Dropping…" : "Drop table"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function CatalogPage() {
  const params = useParams({ strict: false });
  const ws = params.ws as string;
  const schema = params.schema as string | undefined;
  const table = params.table as string | undefined;

  if (schema && table) {
    return <TableDetail ws={ws} schema={schema} table={table} />;
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <h1 className="text-md font-semibold">Catalog</h1>
      </div>
      <div className="flex-1 overflow-hidden">
        <SchemaList ws={ws} />
      </div>
    </div>
  );
}
