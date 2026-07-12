import { Breadcrumb } from "@/components/ui/breadcrumb";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useTables } from "@/queries/schemas";
import { PermissionsPanel } from "@/features/catalog/PermissionsPanel";
import { formatBytes } from "@/utils";

function fmtNum(n: number | null | undefined) {
  return n == null ? "—" : n.toLocaleString();
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

export function SchemaDetail({
  ws,
  catalog,
  schema,
}: {
  ws: string;
  catalog: string;
  schema: string;
}) {
  const { data: tables, isLoading } = useTables(ws, catalog, schema);
  const totalRows = (tables ?? []).reduce((a, t) => a + (t.row_count ?? 0), 0);
  const totalSize = (tables ?? []).reduce((a, t) => a + (t.size_bytes ?? 0), 0);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <Breadcrumb
          items={[
            { label: ws, emphasis: true },
            { label: catalog },
            { label: schema, emphasis: true },
          ]}
        />
        <p className="mt-2 text-xs text-text-secondary">Schema</p>
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
            <div className="grid max-w-lg grid-cols-3 gap-3">
              <Stat label="Tables" value={tables?.length ?? 0} />
              <Stat label="Rows" value={fmtNum(totalRows)} />
              <Stat label="Size" value={formatBytes(totalSize)} />
            </div>
          )}
        </TabsContent>

        <TabsContent
          value="details"
          className="mt-0 flex-1 overflow-auto border-t border-[var(--border-subtle)] p-4"
        >
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-secondary">
            Data per table
          </p>
          <table className="w-full">
            <thead>
              <tr className="border-b border-[var(--border-subtle)] text-left">
                <th className="py-1 pr-3 text-xs font-medium text-text-secondary">
                  Table
                </th>
                <th className="py-1 pr-3 text-xs font-medium text-text-secondary">
                  Rows
                </th>
                <th className="py-1 pr-3 text-xs font-medium text-text-secondary">
                  Size
                </th>
                <th className="py-1 pr-3 text-xs font-medium text-text-secondary">
                  Format
                </th>
                <th className="py-1 pr-3 text-xs font-medium text-text-secondary">
                  Files
                </th>
                <th className="py-1 text-xs font-medium text-text-secondary">
                  Updated
                </th>
              </tr>
            </thead>
            <tbody>
              {(tables ?? []).map((t) => (
                <tr
                  key={t.name}
                  className="border-b border-[var(--border-subtle)]"
                >
                  <td className="py-1.5 pr-3 font-mono text-xs text-text-primary">
                    {t.name}
                  </td>
                  <td className="py-1.5 pr-3 text-xs text-text-secondary">
                    {fmtNum(t.row_count)}
                  </td>
                  <td className="py-1.5 pr-3 text-xs text-text-secondary">
                    {t.size_bytes == null ? "—" : formatBytes(t.size_bytes)}
                  </td>
                  <td className="py-1.5 pr-3 text-xs text-text-secondary">
                    {t.format}
                    {t.format_version != null ? ` v${t.format_version}` : ""}
                  </td>
                  <td className="py-1.5 pr-3 text-xs text-text-secondary">
                    {fmtNum(t.data_file_count)}
                  </td>
                  <td className="py-1.5 text-xs text-text-tertiary">
                    {t.snapshot_at
                      ? new Date(t.snapshot_at).toLocaleString()
                      : "—"}
                  </td>
                </tr>
              ))}
              {tables?.length === 0 && (
                <tr>
                  <td colSpan={6} className="py-2 text-xs text-text-tertiary">
                    No tables in this schema.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </TabsContent>

        <TabsContent
          value="permissions"
          className="mt-0 flex-1 overflow-auto border-t border-[var(--border-subtle)]"
        >
          <PermissionsPanel ws={ws} catalog={catalog} schema={schema} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
