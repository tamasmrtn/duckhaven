import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { LayoutDashboard } from "lucide-react";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDashboards } from "@/queries/dashboards";
import { relativeTime } from "@/utils/relativeTime";
import { DashboardBadges } from "./DashboardBadges";

/**
 * The workspace's dashboards. Readers see the published ones; writers also see
 * drafts nobody has published.
 */
export function DashboardsPage() {
  const { ws } = useParams({ from: "/$ws/dashboards" });
  const navigate = useNavigate();
  const { data: dashboards, isLoading } = useDashboards(ws);

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title="Dashboards"
        description="Numbers, charts and tables from this workspace's data. Each one runs as you, with your access."
      />
      <div className="min-h-0 flex-1 overflow-auto">
        {isLoading ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : !dashboards || dashboards.length === 0 ? (
          <EmptyState
            icon={LayoutDashboard}
            title="No dashboards"
            description="A dashboard is a file of widgets, stored and published through the REST API."
          />
        ) : (
          <Table
            containerClassName="overflow-visible"
            className="table-gutter text-sm"
          >
            <TableHeader className="sticky top-0 z-10 bg-[var(--bg-surface)]">
              <TableRow className="border-b border-[var(--border-subtle)] hover:bg-transparent">
                <TableHead>Dashboard</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Labels</TableHead>
                <TableHead className="text-right">Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {dashboards.map((d) => (
                <TableRow
                  key={d.id}
                  className="cursor-pointer"
                  onClick={() =>
                    void navigate({
                      to: "/$ws/dashboards/$dashboard",
                      params: { ws, dashboard: d.name },
                    })
                  }
                >
                  <TableCell>
                    <Link
                      to="/$ws/dashboards/$dashboard"
                      params={{ ws, dashboard: d.name }}
                      onClick={(e) => e.stopPropagation()}
                      className="font-medium hover:underline"
                    >
                      {d.title}
                    </Link>
                    <div className="font-mono text-2xs text-text-tertiary">
                      {d.name}
                    </div>
                    {d.description && (
                      <div className="text-xs text-text-tertiary">
                        {d.description}
                      </div>
                    )}
                  </TableCell>
                  <TableCell>
                    <DashboardBadges dashboard={d} />
                  </TableCell>
                  <TableCell className="text-xs text-text-secondary">
                    {d.labels.join(", ") || "—"}
                  </TableCell>
                  <TableCell
                    className="text-right text-xs text-text-tertiary"
                    title={new Date(d.updated_at).toLocaleString()}
                  >
                    {relativeTime(d.updated_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </div>
    </div>
  );
}
