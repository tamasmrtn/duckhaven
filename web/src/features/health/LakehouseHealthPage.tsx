import { Link, useParams } from "@tanstack/react-router";
import { HeartPulse } from "lucide-react";
import { EmptyState } from "@/components/app/EmptyState";
import { Skeleton } from "@/components/ui/skeleton";
import { formatBytes, plural } from "@/utils";
import {
  useRecommendations,
  useDismissRecommendation,
  useWorkspaceHealth,
} from "@/queries/maintenance";
import type { HealthBand, TableHealth } from "@/types/maintenance";
import { HealthScoreGauge } from "./HealthScoreGauge";
import { RecommendationCard } from "./RecommendationCard";
import { BAND_COLOR, BAND_LABEL } from "./healthStyles";

function ScoreBadge({
  score,
  band,
}: {
  score: number | null;
  band: HealthBand;
}) {
  const color = BAND_COLOR[band];
  return (
    <span
      className="inline-flex min-w-[2.5rem] justify-center rounded-md px-2 py-0.5 font-mono text-xs font-tabular"
      style={{ color, border: `1px solid ${color}` }}
      title={BAND_LABEL[band]}
    >
      {score ?? "—"}
    </span>
  );
}

function TableRow({ ws, t }: { ws: string; t: TableHealth }) {
  return (
    <Link
      to="/$ws/catalog"
      params={{ ws }}
      className="flex items-center justify-between border-b border-[var(--border-subtle)] px-4 py-2 hover:bg-accent/50"
    >
      <div className="flex items-center gap-3">
        <ScoreBadge score={t.score} band={t.band} />
        <span className="font-mono text-xs text-text-primary">
          {t.schema_name}.{t.table_name}
        </span>
      </div>
      <span className="text-2xs text-text-tertiary font-tabular">
        {formatBytes(t.total_data_bytes)}
        {t.small_file_ratio != null
          ? ` · ${Math.round(t.small_file_ratio * 100)}% small files`
          : ""}
      </span>
    </Link>
  );
}

export function LakehouseHealthPage() {
  const { ws } = useParams({ from: "/$ws/health" });
  const { data: health, isLoading } = useWorkspaceHealth(ws);
  const { data: recommendations = [] } = useRecommendations("open");
  const dismiss = useDismissRecommendation();

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-6 py-4 shrink-0">
        <h1 className="text-md font-semibold">Lakehouse health</h1>
        <p className="mt-0.5 text-xs text-text-secondary">
          Continuous, explainable health scoring and maintenance
          recommendations.
        </p>
      </div>

      <div className="flex-1 overflow-auto p-4">
        {isLoading ? (
          <div className="space-y-4">
            <Skeleton className="h-44 w-full rounded-lg" />
            <Skeleton className="h-64 w-full rounded-lg" />
          </div>
        ) : !health || health.summary.table_count === 0 ? (
          <EmptyState
            icon={HeartPulse}
            title="No health data yet"
            description="The maintenance scanner has not sampled any tables in this workspace. Trigger a scan from Admin → Maintenance, or wait for the next scheduled cycle."
          />
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-6 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-6">
              <HealthScoreGauge
                score={health.summary.score}
                band={health.summary.band}
              />
              <div className="space-y-2">
                <p className="text-sm text-text-secondary">
                  {plural(health.summary.table_count, "table")} scanned ·{" "}
                  {formatBytes(health.summary.total_data_bytes)} ·{" "}
                  <span
                    style={{
                      color:
                        health.summary.attention_count > 0
                          ? "var(--status-failed)"
                          : "var(--status-success)",
                    }}
                  >
                    {health.summary.attention_count} need attention
                  </span>
                </p>
                <div className="flex flex-wrap gap-2">
                  {health.namespaces.map((ns) => (
                    <span
                      key={ns.schema_name}
                      className="rounded-md border border-[var(--border-subtle)] px-2 py-1 text-2xs"
                    >
                      <span className="text-text-secondary">
                        {ns.schema_name}
                      </span>{" "}
                      <ScoreBadge
                        score={ns.summary.score}
                        band={ns.summary.band}
                      />
                    </span>
                  ))}
                </div>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)]">
                <p className="border-b border-[var(--border-subtle)] px-4 py-2 text-xs font-semibold uppercase tracking-wide text-text-secondary">
                  Tables by health
                </p>
                <div>
                  {health.tables.map((t) => (
                    <TableRow
                      key={`${t.schema_name}.${t.table_name}`}
                      ws={ws}
                      t={t}
                    />
                  ))}
                </div>
              </div>

              <div className="space-y-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
                  Recommendations
                </p>
                {recommendations.length === 0 ? (
                  <p className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4 text-sm text-text-tertiary">
                    No open recommendations. Your lakehouse looks well
                    maintained.
                  </p>
                ) : (
                  recommendations.map((rec) => (
                    <RecommendationCard
                      key={rec.id}
                      rec={rec}
                      dismissing={dismiss.isPending}
                      onDismiss={(id) => dismiss.mutate(id)}
                    />
                  ))
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
