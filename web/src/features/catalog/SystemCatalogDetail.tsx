// The detail panes of the `duckhaven` system catalog, its schemas and its tables.
// It is not one of the workspace's catalogs: its tables are fixed and come with
// their columns, it is read-only, and reading it takes the `audit:read` and
// `queries:admin` permissions rather than a grant. So these panes keep the
// layout of the catalog, schema and table panes but leave out what cannot
// apply to it: sizes, history, health, lineage, grants and every write.
import { Link } from "@tanstack/react-router";
import { ExternalLink, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SectionLabel } from "@/components/ui/section-label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError } from "@/api/client";
import { MetaRow, Stat } from "@/features/catalog/CatalogDetail";
import { detailTabsListClass } from "@/features/catalog/detailTabs";
import { selectTemplate } from "@/features/catalog/worksheetSql";
import { ResultsTable } from "@/features/worksheet/ResultsTable";
import { useOpenInWorksheet } from "@/features/worksheet/openInWorksheet";
import { useSystemTableSample } from "@/queries/systemCatalog";
import type { SystemCatalog, SystemTable } from "@/types/systemCatalog";

const UNREACHABLE = "Its database cannot be reached, so a query on it fails";

function schemasOf(system: SystemCatalog): [string, SystemTable[]][] {
  const bySchema = new Map<string, SystemTable[]>();
  for (const t of system.tables) {
    bySchema.set(t.schema_name, [...(bySchema.get(t.schema_name) ?? []), t]);
  }
  return [...bySchema];
}

function Header({
  system,
  kind,
  note,
  children,
}: {
  system: SystemCatalog;
  kind: string;
  note?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-4 py-2 shrink-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Lock className="size-3.5 shrink-0 text-text-secondary" />
          <span className="text-xs text-text-secondary">
            {kind} · read-only
            {note && ` · ${note}`}
            {!system.available && (
              <span title={UNREACHABLE}> · unavailable</span>
            )}
          </span>
        </div>
        {children}
      </div>
    </div>
  );
}

export function SystemCatalogDetail({ system }: { system: SystemCatalog }) {
  const schemas = schemasOf(system);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <Header system={system} kind="System catalog" />

      <Tabs
        defaultValue="overview"
        className="flex flex-1 flex-col overflow-hidden gap-0"
      >
        <TabsList className={detailTabsListClass}>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="details">Details</TabsTrigger>
        </TabsList>

        <TabsContent
          value="overview"
          className="mt-0 flex-1 overflow-auto border-t border-[var(--border-subtle)] p-4"
        >
          <div className="grid max-w-md grid-cols-2 gap-3">
            <Stat label="Schemas" value={schemas.length} />
            <Stat label="Tables" value={system.tables.length} />
          </div>
        </TabsContent>

        <TabsContent
          value="details"
          className="mt-0 flex-1 overflow-auto border-t border-[var(--border-subtle)] p-4"
        >
          <div className="space-y-5">
            <div>
              <SectionLabel className="mb-1">Catalog</SectionLabel>
              <MetaRow label="Name" value={system.catalog} />
              <MetaRow label="Kind" value="System catalog, built in" />
              <MetaRow
                label="Holds"
                value="This deployment's access audit log, current access and query history"
              />
              <MetaRow
                label="Covers"
                value="Every workspace, not only this one"
              />
              <MetaRow
                label="Access"
                value="Read-only. Reading it takes the audit:read and queries:admin permissions."
              />
              <MetaRow
                label="Freshness"
                value="A copy, refreshed every 10 seconds by default"
              />
              <MetaRow
                label="Status"
                value={system.available ? "Available" : UNREACHABLE}
              />
            </div>

            <div>
              <SectionLabel className="mb-2">Tables per schema</SectionLabel>
              <table className="w-full max-w-xl">
                <thead>
                  <tr className="border-b border-[var(--border-subtle)] text-left">
                    <th className="py-1 pr-3 text-xs font-medium text-text-secondary">
                      Schema
                    </th>
                    <th className="py-1 text-xs font-medium text-text-secondary">
                      Tables
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {schemas.map(([name, tables]) => (
                    <tr
                      key={name}
                      className="border-b border-[var(--border-subtle)]"
                    >
                      <td className="py-1.5 pr-3 font-mono text-xs text-text-primary">
                        {name}
                      </td>
                      <td className="py-1.5 font-mono text-xs text-text-secondary">
                        {tables.map((t) => t.name).join(", ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export function SystemSchemaDetail({
  system,
  schema,
}: {
  system: SystemCatalog;
  schema: string;
}) {
  const tables = system.tables.filter((t) => t.schema_name === schema);
  if (tables.length === 0)
    return <div className="p-6 text-text-tertiary">Schema not found.</div>;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <Header system={system} kind="System schema" />

      <Tabs
        defaultValue="overview"
        className="flex flex-1 flex-col overflow-hidden gap-0"
      >
        <TabsList className={detailTabsListClass}>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="details">Details</TabsTrigger>
        </TabsList>

        <TabsContent
          value="overview"
          className="mt-0 flex-1 overflow-auto border-t border-[var(--border-subtle)] p-4"
        >
          <div className="grid max-w-md grid-cols-2 gap-3">
            <Stat label="Tables" value={tables.length} />
          </div>
        </TabsContent>

        <TabsContent
          value="details"
          className="mt-0 flex-1 overflow-auto border-t border-[var(--border-subtle)] p-4"
        >
          <SectionLabel className="mb-2">Tables</SectionLabel>
          <table className="w-full">
            <thead>
              <tr className="border-b border-[var(--border-subtle)] text-left">
                <th className="py-1 pr-3 text-xs font-medium text-text-secondary">
                  Table
                </th>
                <th className="py-1 pr-3 text-xs font-medium text-text-secondary">
                  Holds
                </th>
                <th className="py-1 text-xs font-medium text-text-secondary">
                  Columns
                </th>
              </tr>
            </thead>
            <tbody>
              {tables.map((t) => (
                <tr
                  key={t.name}
                  className="border-b border-[var(--border-subtle)]"
                >
                  <td className="py-1.5 pr-3 font-mono text-xs text-text-primary">
                    {t.name}
                  </td>
                  <td className="py-1.5 pr-3 text-xs text-text-secondary">
                    {t.description}
                  </td>
                  <td className="py-1.5 text-xs text-text-secondary font-tabular">
                    {t.columns.length}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export function SystemTableDetail({
  ws,
  system,
  schema,
  table,
}: {
  ws: string;
  system: SystemCatalog;
  schema: string;
  table: string;
}) {
  const found = system.tables.find(
    (t) => t.schema_name === schema && t.name === table,
  );
  const sample = useSystemTableSample(ws, found ? schema : "", table);
  const openWorksheet = useOpenInWorksheet(ws);
  // 503 when no agent is connected: the same way out as any table's preview.
  const noAgent =
    sample.error instanceof ApiError && sample.error.code === "unavailable";

  if (!found)
    return <div className="p-6 text-text-tertiary">Table not found.</div>;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <Header system={system} kind="System table" note={found.description}>
        <Button
          variant="outline"
          size="sm"
          className="h-7 gap-1.5 text-xs"
          onClick={() =>
            void openWorksheet({
              sql: selectTemplate(schema, table, system.catalog),
              title: table,
            })
          }
        >
          <ExternalLink className="size-3" />
          Query this table
        </Button>
      </Header>

      {/* Side by side from md up, stacked below, as a table's pane is. */}
      <div className="flex flex-1 flex-col gap-0 overflow-auto md:flex-row md:overflow-hidden">
        <div className="max-h-48 w-full shrink-0 overflow-auto border-b border-[var(--border-subtle)] md:max-h-none md:w-80 md:border-b-0 md:border-r">
          <div className="px-4 py-3">
            <SectionLabel className="mb-2">Schema</SectionLabel>
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
                </tr>
              </thead>
              <tbody>
                {found.columns.map((col, i) => (
                  <tr
                    key={col.name}
                    className="border-b border-[var(--border-subtle)]"
                  >
                    <td className="py-1 pr-2 font-mono text-xs text-text-tertiary font-tabular">
                      {i + 1}
                    </td>
                    <td className="py-1 pr-2 font-mono text-xs text-text-primary">
                      {col.name}
                    </td>
                    <td className="py-1 text-xs text-[var(--brand-maya-blue)]">
                      {col.type}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <Tabs
          value="sample"
          className="flex min-h-[20rem] flex-1 flex-col overflow-hidden gap-0 md:min-h-0"
        >
          <TabsList className={detailTabsListClass}>
            <TabsTrigger value="sample">Sample</TabsTrigger>
          </TabsList>
          <TabsContent
            value="sample"
            className="mt-0 flex flex-1 flex-col overflow-hidden"
          >
            <div className="border-b border-t border-[var(--border-subtle)] px-4 py-2 shrink-0">
              <SectionLabel>Sample (LIMIT 20)</SectionLabel>
            </div>
            <div className="flex-1 overflow-hidden">
              <ResultsTable
                columns={sample.data?.columns ?? []}
                columnSchema={sample.data?.column_schema}
                rows={sample.data?.rows ?? []}
                total={sample.data?.total ?? 0}
                isLoading={sample.isLoading}
                error={sample.error ? sample.error.message : null}
                errorTitle="Can't preview this table"
                errorActions={
                  noAgent ? (
                    <Link
                      to="/$ws/compute"
                      params={{ ws }}
                      className="text-xs font-medium text-[var(--link)] hover:underline"
                    >
                      Manage compute
                    </Link>
                  ) : undefined
                }
              />
            </div>
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
