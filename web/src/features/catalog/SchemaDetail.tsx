import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  detailTabClass,
  detailTabsListClass,
} from "@/features/catalog/detailTabs";
import { useTables } from "@/queries/schemas";
import { PermissionsPanel } from "@/features/catalog/PermissionsPanel";
import {
  formatTableSize,
  INLINED_HINT,
  isInlined,
  totalTableSize,
} from "@/features/catalog/tableSize";
import { formatBytes } from "@/utils";

function fmtNum(n: number | null | undefined) {
  return n == null ? "—" : n.toLocaleString();
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    // Grows with its value: a large schema's row count is wider than a fixed
    // third of the row.
    <div className="min-w-[10rem] rounded-md border border-[var(--border-subtle)] px-4 py-3">
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
  const size = totalTableSize(tables ?? []);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-4 py-2 shrink-0">
        <p className="text-xs text-text-secondary">Schema</p>
      </div>

      <Tabs
        defaultValue="overview"
        className="flex flex-1 flex-col overflow-hidden gap-0"
      >
        <TabsList className={detailTabsListClass}>
          <TabsTrigger value="overview" className={detailTabClass}>
            Overview
          </TabsTrigger>
          <TabsTrigger value="details" className={detailTabClass}>
            Details
          </TabsTrigger>
          <TabsTrigger value="permissions" className={detailTabClass}>
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
            <div className="flex flex-wrap gap-3">
              <Stat label="Tables" value={tables?.length ?? 0} />
              <Stat label="Rows" value={fmtNum(totalRows)} />
              <Stat
                label={
                  size.known > 0 && size.known < size.count
                    ? `Size · ${size.known} of ${size.count} tables`
                    : "Size"
                }
                value={formatBytes(size.bytes)}
              />
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
                    <span title={isInlined(t) ? INLINED_HINT : undefined}>
                      {formatTableSize(t)}
                    </span>
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
