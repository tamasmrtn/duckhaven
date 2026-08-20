import { useEffect, useState } from "react";
import { useParams, useNavigate, useSearch } from "@tanstack/react-router";
import { Pencil, ExternalLink, Trash2, Clock } from "lucide-react";
import { toast } from "sonner";
import { Breadcrumb } from "@/components/ui/breadcrumb";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError } from "@/api/client";
import { useTable, useTableSample } from "@/queries/schemas";
import { useDeleteTable } from "@/queries/schemas.mutations";
import { ResultsTable } from "@/features/worksheet/ResultsTable";
import { StorageIcon } from "@/components/app/StorageIcon";
import { useWorkspace } from "@/queries/workspaces";
import { CatalogTree } from "@/features/catalog/CatalogTree";
import { CatalogDetail } from "@/features/catalog/CatalogDetail";
import { SchemaDetail } from "@/features/catalog/SchemaDetail";
import { ConfirmDropDialog } from "@/features/catalog/ConfirmDropDialog";
import { SnapshotHistoryPanel } from "@/features/catalog/SnapshotHistoryPanel";
import { PermissionsPanel } from "@/features/catalog/PermissionsPanel";
import { TableHealthPanel } from "@/features/health/TableHealthPanel";
import { LineagePanel } from "@/features/lineage/LineagePanel";
import { SemanticPanel } from "@/features/semantic/SemanticPanel";
import { BrokenByDropWarning } from "@/features/semantic/BrokenByDropWarning";
import {
  alterTemplate,
  selectTemplate,
  stashWorksheetSql,
} from "@/features/catalog/worksheetSql";
import { formatBytes } from "@/utils";
import {
  recordRecentlyViewed,
  getRecentlyViewed,
} from "@/utils/recentlyViewed";
import { objectPath } from "@/utils/objectPath";
import type { CatalogTable } from "@/types/catalog";

function formatNumber(n: number | null) {
  if (n == null) return "—";
  return n.toLocaleString();
}

/** Iceberg-native facts for the table-detail header: format version, current
 * snapshot, data-file count, and a has-deletes badge. Renders nothing when no
 * Iceberg metadata has been captured yet. */
function IcebergMetaLine({ table }: { table: CatalogTable }) {
  const parts: string[] = [];
  if (table.format_version != null)
    parts.push(`Iceberg v${table.format_version}`);
  if (table.snapshot_id) parts.push(`snapshot ${table.snapshot_id}`);
  if (table.data_file_count != null)
    parts.push(`${formatNumber(table.data_file_count)} files`);
  if (table.snapshot_at)
    parts.push(`updated ${new Date(table.snapshot_at).toLocaleString()}`);
  if (parts.length === 0 && !table.has_deletes) return null;
  return (
    <p className="flex items-center gap-1.5 text-xs text-text-tertiary">
      {parts.length > 0 && <span>{parts.join(" · ")}</span>}
      {table.has_deletes && (
        <span className="rounded border border-[var(--border-subtle)] px-1.5 py-0.5 font-medium text-text-secondary">
          has deletes
        </span>
      )}
    </p>
  );
}

function TableDetail({
  ws,
  catalog,
  schema,
  table,
  tab,
}: {
  ws: string;
  catalog: string;
  schema: string;
  table: string;
  tab?: string;
}) {
  const { data: tableData, isLoading } = useTable(ws, catalog, schema, table);
  const {
    data: sampleData,
    isLoading: sampleLoading,
    error: sampleError,
  } = useTableSample(ws, catalog, schema, table);
  // In a scoped catalog a `metadata`-tier grant lets you describe a table but
  // not read its rows: the sample endpoint 404s (leaf denials 404 so they don't
  // confirm existence). Since the table detail already loaded, that 404/403 here
  // means "no read access", not "missing" — say so instead of an empty grid.
  const sampleDenied =
    sampleError instanceof ApiError &&
    (sampleError.status === 404 || sampleError.status === 403);
  const { data: workspace } = useWorkspace(ws);
  const navigate = useNavigate();
  const deleteTable = useDeleteTable(ws, catalog, schema);
  const [dropOpen, setDropOpen] = useState(false);

  useEffect(() => {
    recordRecentlyViewed(ws, { type: "table", catalog, schema, name: table });
  }, [ws, catalog, schema, table]);

  function openInWorksheet(sql: string) {
    stashWorksheetSql(ws, sql);
    navigate({ to: "/$ws/worksheets", params: { ws } });
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
        <Breadcrumb
          items={[
            { label: ws, emphasis: true },
            { label: catalog },
            { label: schema },
            { label: table, emphasis: true },
          ]}
        />

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
            <IcebergMetaLine table={tableData} />
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-7 gap-1.5 text-xs"
              onClick={() =>
                openInWorksheet(alterTemplate(schema, table, catalog))
              }
            >
              <Pencil className="size-3" />
              Alter table
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-7 gap-1.5 text-xs"
              onClick={() =>
                openInWorksheet(selectTemplate(schema, table, catalog))
              }
            >
              <ExternalLink className="size-3" />
              Query this table
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-7 gap-1.5 text-xs"
              onClick={() => setDropOpen(true)}
            >
              <Trash2 className="size-3" />
              Drop
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

        {/* Sample / History tabs */}
        <Tabs
          value={tab ?? "sample"}
          onValueChange={(v) =>
            navigate({
              to: "/$ws/catalog/$catalog/$schema/$table",
              params: { ws, catalog, schema, table },
              search: (prev) => ({ ...prev, tab: v }),
              replace: true,
            })
          }
          className="flex flex-1 flex-col overflow-hidden gap-0"
        >
          <TabsList className="m-2 h-8 w-fit shrink-0">
            <TabsTrigger value="sample" className="text-xs">
              Sample
            </TabsTrigger>
            <TabsTrigger value="history" className="text-xs">
              History
            </TabsTrigger>
            <TabsTrigger value="health" className="text-xs">
              Health
            </TabsTrigger>
            <TabsTrigger value="lineage" className="text-xs">
              Lineage
            </TabsTrigger>
            <TabsTrigger value="semantics" className="text-xs">
              Semantics
            </TabsTrigger>
            <TabsTrigger value="permissions" className="text-xs">
              Permissions
            </TabsTrigger>
          </TabsList>
          <TabsContent
            value="sample"
            className="mt-0 flex flex-1 flex-col overflow-hidden"
          >
            <div className="border-b border-t border-[var(--border-subtle)] px-4 py-2 shrink-0">
              <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
                Sample (LIMIT 20)
              </p>
            </div>
            <div className="flex-1 overflow-hidden">
              {sampleDenied ? (
                <div className="flex h-full items-center justify-center p-6 text-center">
                  <p className="max-w-sm text-sm text-text-tertiary">
                    You have <strong>metadata</strong> access to this table —
                    you can see its structure, but previewing rows requires{" "}
                    <strong>reader</strong> access. Ask a workspace owner to
                    grant it.
                  </p>
                </div>
              ) : (
                <ResultsTable
                  columns={sampleData?.columns ?? []}
                  columnSchema={sampleData?.column_schema}
                  rows={sampleData?.rows ?? []}
                  total={sampleData?.total ?? 0}
                  isLoading={sampleLoading}
                />
              )}
            </div>
          </TabsContent>
          <TabsContent
            value="history"
            className="mt-0 flex flex-1 flex-col overflow-hidden border-t border-[var(--border-subtle)]"
          >
            <SnapshotHistoryPanel
              ws={ws}
              catalog={catalog}
              schema={schema}
              table={table}
              onQuery={openInWorksheet}
            />
          </TabsContent>
          <TabsContent
            value="health"
            className="mt-0 flex flex-1 flex-col overflow-hidden border-t border-[var(--border-subtle)]"
          >
            <TableHealthPanel ws={ws} schema={schema} table={table} />
          </TabsContent>
          <TabsContent
            value="lineage"
            className="mt-0 flex flex-1 flex-col overflow-hidden border-t border-[var(--border-subtle)]"
          >
            <LineagePanel
              ws={ws}
              catalog={catalog}
              schema={schema}
              table={table}
            />
          </TabsContent>
          <TabsContent
            value="semantics"
            className="mt-0 flex flex-1 flex-col overflow-auto border-t border-[var(--border-subtle)]"
          >
            <SemanticPanel
              ws={ws}
              catalog={catalog}
              schema={schema}
              table={table}
            />
          </TabsContent>
          <TabsContent
            value="permissions"
            className="mt-0 flex flex-1 flex-col overflow-auto border-t border-[var(--border-subtle)]"
          >
            <PermissionsPanel
              ws={ws}
              catalog={catalog}
              schema={schema}
              table={table}
            />
          </TabsContent>
        </Tabs>
      </div>

      <ConfirmDropDialog
        open={dropOpen}
        onOpenChange={setDropOpen}
        kind="table"
        name={table}
        pending={deleteTable.isPending}
        warning={
          <BrokenByDropWarning
            ws={ws}
            catalog={catalog}
            schema={schema}
            table={table}
          />
        }
        onConfirm={async () => {
          await deleteTable.mutateAsync(table);
          toast.success(`Dropped ${schema}.${table}`);
          navigate({ to: "/$ws/catalog", params: { ws } });
        }}
      />
    </div>
  );
}

export function CatalogPage() {
  const params = useParams({ strict: false });
  const ws = params.ws as string;
  const catalog = params.catalog as string | undefined;
  const schema = params.schema as string | undefined;
  const table = params.table as string | undefined;
  const search = useSearch({ strict: false });
  const tab = typeof search.tab === "string" ? search.tab : undefined;
  const navigate = useNavigate();
  const { data: workspace } = useWorkspace(ws);
  // Only relevant on the bare landing state (nothing selected yet) — no need
  // to read localStorage on every render once a catalog/schema/table is picked.
  const recentlyViewed = !catalog ? getRecentlyViewed(ws) : [];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <h1 className="text-md font-semibold">Catalog</h1>
      </div>
      <div className="flex flex-1 overflow-hidden">
        {/* Catalog tree — the same component the worksheet sidebar uses */}
        <div className="w-64 shrink-0 overflow-hidden border-r border-[var(--border-subtle)] bg-[var(--bg-surface)]">
          <CatalogTree
            ws={ws}
            workspaceName={workspace?.name ?? ws}
            onCatalogClick={(c) =>
              navigate({
                to: "/$ws/catalog/$catalog",
                params: { ws, catalog: c },
              })
            }
            onSchemaClick={(c, s) =>
              navigate({
                to: "/$ws/catalog/$catalog/$schema",
                params: { ws, catalog: c, schema: s },
              })
            }
            onTableClick={(c, s, t) =>
              navigate({
                to: "/$ws/catalog/$catalog/$schema/$table",
                params: { ws, catalog: c, schema: s, table: t },
              })
            }
          />
        </div>

        <div className="flex-1 overflow-hidden">
          {catalog && schema && table ? (
            <TableDetail
              ws={ws}
              catalog={catalog}
              schema={schema}
              table={table}
              tab={tab}
            />
          ) : catalog && schema ? (
            <SchemaDetail ws={ws} catalog={catalog} schema={schema} />
          ) : catalog ? (
            <CatalogDetail ws={ws} catalog={catalog} />
          ) : recentlyViewed.length > 0 ? (
            <div className="flex h-full flex-col gap-2 overflow-auto p-6">
              <p className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
                Recently viewed
              </p>
              <div className="max-w-sm space-y-0.5">
                {recentlyViewed.map((r) => (
                  <button
                    key={`recent-${r.type}-${r.catalog}-${r.schema}-${r.name}`}
                    type="button"
                    onClick={() =>
                      navigate({
                        to: objectPath(ws, {
                          type: r.type,
                          catalog: r.catalog,
                          schema_name: r.schema,
                          name: r.name,
                        }),
                      } as Parameters<typeof navigate>[0])
                    }
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-text-primary hover:bg-accent"
                  >
                    <Clock className="size-4 shrink-0 text-text-secondary" />
                    <span className="truncate">{r.name}</span>
                    <span className="ml-auto truncate text-2xs text-text-tertiary">
                      {r.catalog}.{r.schema}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-text-tertiary">
              Select a catalog, schema, or table to view its details.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
