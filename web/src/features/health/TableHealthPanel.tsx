import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { HeartPulse } from "lucide-react";
import { EmptyState } from "@/components/ui/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useDismissRecommendation,
  useTableHealth,
} from "@/queries/maintenance";
import type { HealthFactor } from "@/types/maintenance";
import { HealthScoreGauge } from "./HealthScoreGauge";
import { RecommendationCard } from "./RecommendationCard";
import { BAND_COLOR } from "./healthStyles";

const DIMENSION_LABEL: Record<string, string> = {
  fragmentation: "Fragmentation",
  snapshots: "Snapshot hygiene",
  metadata: "Metadata health",
  storage: "Storage efficiency",
};

function FactorBar({ name, factor }: { name: string; factor: HealthFactor }) {
  const color =
    factor.score >= 90
      ? BAND_COLOR.healthy
      : factor.score >= 70
        ? BAND_COLOR.fair
        : BAND_COLOR.attention;
  return (
    <div>
      <div className="flex items-center justify-between text-xs">
        <span className="text-text-secondary">
          {DIMENSION_LABEL[name] ?? name}
        </span>
        <span className="font-mono font-tabular" style={{ color }}>
          {factor.score}
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full rounded-full bg-[var(--border-subtle)]">
        <div
          className="h-1.5 rounded-full"
          style={{ width: `${factor.score}%`, background: color }}
        />
      </div>
      <p className="mt-1 text-2xs text-text-tertiary">{factor.detail}</p>
    </div>
  );
}

export function TableHealthPanel({
  ws,
  catalog,
  schema,
  table,
}: {
  ws: string;
  catalog: string;
  schema: string;
  table: string;
}) {
  const { data, isLoading, isError } = useTableHealth(
    ws,
    catalog,
    schema,
    table,
  );
  const dismiss = useDismissRecommendation();

  if (isLoading) {
    return (
      <div className="space-y-4 p-4">
        <Skeleton className="h-40 w-full rounded-lg" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <EmptyState
        icon={HeartPulse}
        title="No health data yet"
        description="This table has not been scanned. Trigger a scan from Admin → Maintenance."
      />
    );
  }

  const { table: t, history, recommendations } = data;
  const factors = t.factors ?? {};
  const trend = history.map((h) => ({
    t: Date.parse(h.scanned_at),
    bytes: h.total_data_bytes,
  }));

  return (
    <div className="flex-1 overflow-auto p-4">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-6 rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
          <HealthScoreGauge score={t.score} band={t.band} size={120} />
          <div className="min-w-[240px] flex-1 space-y-3">
            {Object.entries(factors).map(([name, factor]) => (
              <FactorBar key={name} name={name} factor={factor} />
            ))}
            {Object.keys(factors).length === 0 && (
              <p className="text-sm text-text-tertiary">
                No scored dimensions for this table yet.
              </p>
            )}
          </div>
        </div>

        {trend.length > 1 && (
          <div className="rounded-lg border border-[var(--border-subtle)] bg-[var(--bg-surface)] p-4">
            <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-text-secondary">
              Storage growth
            </p>
            <div className="h-48" data-testid="growth-chart">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={trend}>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="var(--border-subtle)"
                  />
                  <XAxis
                    dataKey="t"
                    type="number"
                    scale="time"
                    domain={["dataMin", "dataMax"]}
                    tickFormatter={(v) =>
                      new Date(Number(v)).toLocaleDateString()
                    }
                    tick={{ fontSize: 11, fill: "var(--text-tertiary)" }}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: "var(--text-tertiary)" }}
                    width={60}
                    tickFormatter={(v) =>
                      `${(Number(v) / 1024 ** 3).toFixed(1)}G`
                    }
                  />
                  <Tooltip
                    labelFormatter={(l) => new Date(Number(l)).toLocaleString()}
                    formatter={(v) =>
                      `${(Number(v) / 1024 ** 3).toFixed(2)} GB`
                    }
                  />
                  <Line
                    type="monotone"
                    dataKey="bytes"
                    stroke="var(--brand-maya-blue)"
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}

        {recommendations.length > 0 && (
          <div className="space-y-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
              Recommendations
            </p>
            {recommendations.map((rec) => (
              <RecommendationCard
                key={rec.id}
                rec={rec}
                showTable={false}
                dismissing={dismiss.isPending}
                onDismiss={(id) => dismiss.mutate(id)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
