import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";
import type { HealthBand } from "@/types/maintenance";
import { BAND_COLOR, BAND_LABEL } from "./healthStyles";

interface Props {
  score: number | null;
  band: HealthBand;
  size?: number;
}

/** A donut gauge with the score in the centre, coloured by health band. */
export function HealthScoreGauge({ score, band, size = 160 }: Props) {
  const value = score ?? 0;
  const color = BAND_COLOR[band];
  const data = [
    { name: "score", value },
    { name: "rest", value: Math.max(0, 100 - value) },
  ];
  return (
    <div
      className="relative shrink-0"
      style={{ width: size, height: size }}
      data-testid="health-gauge"
      role="img"
      aria-label={`Health score ${score ?? "unknown"} of 100, ${BAND_LABEL[band]}`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            innerRadius="72%"
            outerRadius="100%"
            startAngle={90}
            endAngle={-270}
            stroke="none"
            isAnimationActive={false}
          >
            <Cell fill={color} />
            <Cell fill="var(--border-subtle)" />
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="font-mono text-3xl font-tabular" style={{ color }}>
          {score ?? "—"}
        </span>
        <span className="text-2xs uppercase tracking-wide text-text-tertiary">
          {BAND_LABEL[band]}
        </span>
      </div>
    </div>
  );
}
