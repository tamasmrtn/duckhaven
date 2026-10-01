import { useIsDark } from "@/hooks/useIsDark";
import type { MetricsSample } from "@/types/agent";
import { RESOURCE, resolve } from "./monitoring/chartColors";

const DASH = "—";
const WIDTH = 64;
const HEIGHT = 18;

/**
 * CPU over the live buffer (about five minutes of 2-second samples), as a
 * word-sized line: enough to see "busy now" versus "was busy a minute ago" in a
 * list of agents without opening each one. The number beside it is the latest
 * reading; the label carries the peak for anyone not reading the shape.
 */
export function CpuCell({ samples }: { samples: MetricsSample[] }) {
  const dark = useIsDark();
  const latest = samples[samples.length - 1];
  if (!latest) return <span className="text-text-tertiary">{DASH}</span>;
  const values = samples.map((s) => s.cpu_percent);
  const peak = Math.max(...values);
  const step = values.length > 1 ? WIDTH / (values.length - 1) : 0;
  const points = values
    .map(
      (v, i) =>
        `${(i * step).toFixed(1)},${(HEIGHT - (v / 100) * HEIGHT).toFixed(1)}`,
    )
    .join(" ");
  return (
    <span className="flex items-center gap-2">
      <svg
        width={WIDTH}
        height={HEIGHT}
        role="img"
        aria-label={`CPU over the last few minutes: now ${Math.round(latest.cpu_percent)}%, peak ${Math.round(peak)}%`}
        className="shrink-0 overflow-visible"
      >
        <line
          x1={0}
          x2={WIDTH}
          y1={HEIGHT}
          y2={HEIGHT}
          stroke="var(--border-subtle)"
        />
        <polyline
          points={points}
          fill="none"
          stroke={resolve(RESOURCE.cpu, dark)}
          strokeWidth={1.5}
          strokeLinejoin="round"
          data-testid="cpu-sparkline"
        />
      </svg>
      <span className="font-mono text-xs font-tabular">
        {Math.round(latest.cpu_percent)}%
      </span>
    </span>
  );
}

/** Memory now, as a bar against the agent's limit, with the number beside it. */
export function MemoryCell({ sample }: { sample: MetricsSample | undefined }) {
  const dark = useIsDark();
  if (!sample) return <span className="text-text-tertiary">{DASH}</span>;
  const pct = Math.max(0, Math.min(100, sample.memory_percent));
  return (
    <span className="flex items-center gap-2">
      <span
        className="h-1.5 w-12 shrink-0 overflow-hidden rounded-full bg-[var(--border-subtle)]"
        aria-hidden
      >
        <span
          className="block h-full rounded-full"
          style={{
            width: `${pct}%`,
            backgroundColor: resolve(RESOURCE.memory, dark),
          }}
        />
      </span>
      <span className="font-mono text-xs font-tabular">{Math.round(pct)}%</span>
    </span>
  );
}

/** A live count, or "—" when the agent isn't reporting (or can't measure it). */
export function CountCell({ value }: { value: number | null | undefined }) {
  return (
    <span
      className={
        value == null ? "text-text-tertiary" : "font-mono text-xs font-tabular"
      }
    >
      {value == null ? DASH : value}
    </span>
  );
}
