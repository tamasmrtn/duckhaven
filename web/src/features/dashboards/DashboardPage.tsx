import { useEffect, useMemo, type CSSProperties } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearch,
} from "@tanstack/react-router";
import {
  AlertTriangle,
  GitBranch,
  LayoutDashboard,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { ApiError } from "@/api/client";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { useDashboard } from "@/queries/dashboards";
import type { Dashboard, RowSpec } from "@/types/dashboard";
import { plural } from "@/utils";
import { DashboardBadges } from "./DashboardBadges";
import { DashboardFilters } from "./DashboardFilters";
import { rowLayout } from "./layout";
import { runParams, type DashboardSearch } from "./params";
import { useDashboardRun, type RunView } from "./useDashboardRun";
import type { WidgetContext } from "./widgets/WidgetChrome";
import { WidgetCard } from "./widgets/WidgetCard";

function Row({
  row,
  run,
  context,
}: {
  row: RowSpec;
  run: RunView;
  context: WidgetContext;
}) {
  const { widths, rest } = rowLayout(row.widgets);
  return (
    // Side by side from md up, each widget taking its share of 12 columns;
    // stacked on a phone.
    <div className="flex flex-col gap-4 md:flex-row">
      {row.widgets.map((spec, i) => (
        <div
          key={spec.id}
          className="min-w-0 md:[flex:var(--share)_1_0%]"
          style={{ "--share": widths[i] } as CSSProperties}
        >
          <WidgetCard
            spec={spec}
            slot={run.widgets[spec.id]}
            context={context}
          />
        </div>
      ))}
      {rest > 0 && (
        <div
          aria-hidden
          className="hidden md:block md:[flex:var(--share)_1_0%]"
          style={{ "--share": rest } as CSSProperties}
        />
      )}
    </div>
  );
}

function RunStatus({ run }: { run: RunView }) {
  if (run.running) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <Loader2 className="size-3 animate-spin" aria-hidden />
        Running as you
      </span>
    );
  }
  if (!run.done) return null;
  const answered = Object.values(run.done.widgets).reduce(
    (a, b) => a + (b ?? 0),
    0,
  );
  const seconds = (run.done.elapsed_ms / 1000).toFixed(1);
  const { cache_hits: hits, statements } = run.done;
  const cached =
    hits > 0 ? `; ${hits} of ${statements} queries from the cache` : "";
  return (
    <span>
      {plural(answered, "widget")} answered in {seconds} s{cached}
    </span>
  );
}

function Banners({ dashboard, run }: { dashboard: Dashboard; run: RunView }) {
  return (
    <>
      {dashboard.managed && (
        <Banner>
          <GitBranch className="size-3.5 shrink-0" aria-hidden />
          <span>
            Managed in git
            {dashboard.source_id && (
              <>
                {" "}
                as <span className="font-mono">{dashboard.source_id}</span>
              </>
            )}
            . Change it by changing its file there and applying it.
          </span>
        </Banner>
      )}
      {dashboard.draft && (
        <Banner>
          <span>
            <strong className="font-semibold">Draft.</strong> Nobody has
            published this dashboard, so only writers can see it.
          </span>
        </Banner>
      )}
      {run.running && run.waiting.length > 0 && (
        <Banner>
          <Loader2 className="size-3.5 shrink-0 animate-spin" aria-hidden />
          <span>
            Compute is starting. {plural(run.waiting.length, "widget")}{" "}
            {run.waiting.length === 1 ? "waits" : "wait"} for it.
          </span>
        </Banner>
      )}
      {run.error && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-md border border-[var(--status-failed)]/40 bg-[var(--status-failed)]/10 px-3 py-2 text-xs text-text-secondary"
        >
          <AlertTriangle
            className="size-3.5 shrink-0 text-[var(--status-failed)]"
            aria-hidden
          />
          <span>{run.error.message}</span>
        </div>
      )}
    </>
  );
}

function Loaded({ ws, dashboard }: { ws: string; dashboard: Dashboard }) {
  const search = useSearch({ from: "/$ws/dashboards/$dashboard" });
  const navigate = useNavigate();
  const request = useMemo(() => ({ params: runParams(search) }), [search]);
  const run = useDashboardRun(ws, dashboard.name, request);
  const spec = dashboard.spec;
  const context = useMemo<WidgetContext>(() => {
    const values = new Map(
      (run.start?.parameters ?? []).map((p) => [p.name, p.value]),
    );
    return {
      ws,
      dashboard: dashboard.name,
      parameters: Object.fromEntries(
        Object.entries(spec.parameters ?? {}).map(([name, p]) => [
          name,
          { label: p.label ?? name, type: p.type, value: values.get(name) },
        ]),
      ),
    };
  }, [ws, dashboard.name, spec.parameters, run.start]);

  // A copied link to one widget lands on it.
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (id.startsWith("widget-")) document.getElementById(id)?.scrollIntoView();
  }, []);

  function setSearch(next: DashboardSearch) {
    void navigate({
      to: "/$ws/dashboards/$dashboard",
      params: { ws, dashboard: dashboard.name },
      search: next,
      replace: true,
    });
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={spec.title}
        description={spec.description ?? undefined}
        badge={<DashboardBadges dashboard={dashboard} />}
        actions={
          <div className="flex items-center gap-3 text-2xs text-text-tertiary">
            <RunStatus run={run} />
            <Button
              variant="outline"
              size="sm"
              className="h-7 gap-1.5 text-xs"
              onClick={run.refresh}
              disabled={run.running}
            >
              <RefreshCw className="size-3.5" aria-hidden />
              Refresh
            </Button>
          </div>
        }
      />
      <DashboardFilters
        ws={ws}
        parameters={spec.parameters ?? {}}
        search={search}
        onChange={setSearch}
      />
      <div className="min-h-0 flex-1 overflow-auto">
        <div className="flex flex-col gap-4 p-4 md:p-6">
          <Banners dashboard={dashboard} run={run} />
          {spec.rows.map((row, i) => (
            <Row key={i} row={row} run={run} context={context} />
          ))}
        </div>
      </div>
    </div>
  );
}

/** One dashboard, run as the person viewing it. */
export function DashboardPage() {
  const { ws, dashboard: name } = useParams({
    from: "/$ws/dashboards/$dashboard",
  });
  const { data, isLoading, error } = useDashboard(ws, name);

  if (isLoading) {
    return (
      <div className="space-y-4 p-6">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }
  if (!data) {
    const missing = error instanceof ApiError && error.status === 404;
    return (
      <EmptyState
        icon={LayoutDashboard}
        title={
          missing
            ? `No dashboard called ${name}`
            : "This dashboard could not be read"
        }
        description={
          missing
            ? "It may have been deleted, or not published yet."
            : error instanceof Error
              ? error.message
              : undefined
        }
        action={
          <Link
            to="/$ws/dashboards"
            params={{ ws }}
            className="text-xs text-[var(--brand-slate-blue)] underline"
          >
            Every dashboard
          </Link>
        }
      />
    );
  }
  // Keyed by name, so following a link to another dashboard starts afresh.
  return <Loaded key={data.name} ws={ws} dashboard={data} />;
}
