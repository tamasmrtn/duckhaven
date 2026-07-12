import { Breadcrumb } from "@/components/ui/breadcrumb";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCatalogs } from "@/queries/catalogs";
import { useSchemas, useTables } from "@/queries/schemas";
import { StorageIcon } from "@/components/app/StorageIcon";
import { PermissionsPanel } from "@/features/catalog/PermissionsPanel";
import { backendLabel } from "@/features/catalog/CatalogInfoDialog";
import { formatBytes } from "@/utils";
import type { BackendKind } from "@/types/storage-backend";

function fmtNum(n: number | null | undefined) {
  return n == null ? "—" : n.toLocaleString();
}

/** One row of the per-schema breakdown: fetches the schema's tables to sum
 * their count and on-disk size. */
function SchemaStatsRow({
  ws,
  catalog,
  schema,
}: {
  ws: string;
  catalog: string;
  schema: string;
}) {
  const { data: tables, isLoading } = useTables(ws, catalog, schema);
  const count = tables?.length ?? 0;
  const size = (tables ?? []).reduce((a, t) => a + (t.size_bytes ?? 0), 0);
  const rows = (tables ?? []).reduce((a, t) => a + (t.row_count ?? 0), 0);
  return (
    <tr className="border-b border-[var(--border-subtle)]">
      <td className="py-1.5 pr-3 font-mono text-xs text-text-primary">
        {schema}
      </td>
      <td className="py-1.5 pr-3 text-xs text-text-secondary">
        {isLoading ? "…" : count}
      </td>
      <td className="py-1.5 pr-3 text-xs text-text-secondary">
        {isLoading ? "…" : fmtNum(rows)}
      </td>
      <td className="py-1.5 text-xs text-text-secondary">
        {isLoading ? "…" : formatBytes(size)}
      </td>
    </tr>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border border-[var(--border-subtle)] px-4 py-3">
      <p className="text-2xl font-semibold text-text-primary font-tabular">
        {value}
      </p>
      <p className="text-xs text-text-tertiary">{label}</p>
    </div>
  );
}

function MetaRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3 py-1.5 text-sm">
      <span className="w-40 shrink-0 text-text-tertiary">{label}</span>
      <span className="min-w-0 break-all text-text-secondary">{value}</span>
    </div>
  );
}

export function CatalogDetail({
  ws,
  catalog,
}: {
  ws: string;
  catalog: string;
}) {
  const { data: catalogs } = useCatalogs(ws);
  const cat = catalogs?.find((c) => c.slug === catalog);
  const { data: schemas, isLoading } = useSchemas(ws, catalog);

  const baseLocation = cat
    ? `${(cat.storage_backend_root_uri || "").replace(/\/$/, "")}/${cat.polaris_name}`.replace(
        /^\//,
        "",
      )
    : "";

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <Breadcrumb
          items={[
            { label: ws, emphasis: true },
            { label: catalog, emphasis: true },
          ]}
        />
        <div className="mt-2 flex items-center gap-2">
          {cat && (
            <StorageIcon
              kind={cat.storage_backend_kind as BackendKind}
              className="text-text-secondary"
            />
          )}
          <span className="text-xs text-text-secondary">
            Catalog · {cat ? backendLabel(cat.storage_backend_kind) : ""}
            {cat?.access_mode === "scoped" && " · scoped access"}
          </span>
        </div>
      </div>

      <Tabs
        defaultValue="overview"
        className="flex flex-1 flex-col overflow-hidden gap-0"
      >
        <TabsList className="m-2 h-8 w-fit shrink-0">
          <TabsTrigger value="overview" className="text-xs">
            Overview
          </TabsTrigger>
          <TabsTrigger value="details" className="text-xs">
            Details
          </TabsTrigger>
          <TabsTrigger value="permissions" className="text-xs">
            Permissions
          </TabsTrigger>
        </TabsList>

        <TabsContent
          value="overview"
          className="mt-0 flex-1 overflow-auto border-t border-[var(--border-subtle)] p-4"
        >
          {isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : (
            <div className="grid max-w-md grid-cols-2 gap-3">
              <Stat label="Schemas" value={schemas?.length ?? 0} />
              <Stat
                label="Access mode"
                value={cat?.access_mode === "scoped" ? "Scoped" : "Open"}
              />
            </div>
          )}
        </TabsContent>

        <TabsContent
          value="details"
          className="mt-0 flex-1 overflow-auto border-t border-[var(--border-subtle)] p-4"
        >
          <div className="space-y-5">
            <div>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-text-secondary">
                Catalog
              </p>
              <MetaRow label="Name" value={cat?.name ?? catalog} />
              <MetaRow label="Polaris name" value={cat?.polaris_name ?? "—"} />
              <MetaRow
                label="Storage backend"
                value={
                  cat
                    ? `${cat.storage_backend_name} (${backendLabel(cat.storage_backend_kind)})`
                    : "—"
                }
              />
              <MetaRow
                label="Root URI"
                value={cat?.storage_backend_root_uri || "—"}
              />
              <MetaRow label="Base location" value={baseLocation || "—"} />
              <MetaRow
                label="Default catalog"
                value={cat?.is_default ? "Yes" : "No"}
              />
              <MetaRow
                label="Attached workspaces"
                value={fmtNum(cat?.attached_workspaces ?? null)}
              />
            </div>

            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-secondary">
                Data per schema
              </p>
              <table className="w-full max-w-xl">
                <thead>
                  <tr className="border-b border-[var(--border-subtle)] text-left">
                    <th className="py-1 pr-3 text-xs font-medium text-text-secondary">
                      Schema
                    </th>
                    <th className="py-1 pr-3 text-xs font-medium text-text-secondary">
                      Tables
                    </th>
                    <th className="py-1 pr-3 text-xs font-medium text-text-secondary">
                      Rows
                    </th>
                    <th className="py-1 text-xs font-medium text-text-secondary">
                      Size
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {(schemas ?? []).map((s) => (
                    <SchemaStatsRow
                      key={s.name}
                      ws={ws}
                      catalog={catalog}
                      schema={s.name}
                    />
                  ))}
                  {schemas?.length === 0 && (
                    <tr>
                      <td
                        colSpan={4}
                        className="py-2 text-xs text-text-tertiary"
                      >
                        No user schemas.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </TabsContent>

        <TabsContent
          value="permissions"
          className="mt-0 flex-1 overflow-auto border-t border-[var(--border-subtle)]"
        >
          <PermissionsPanel ws={ws} catalog={catalog} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
