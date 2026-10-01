import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AgentMonitoring, MonitoringWindow } from "@/types/agent";
import { MONITORING_WINDOWS } from "@/types/agent";
import { formatAbsoluteTimestamp } from "../metricsTime";

export const WINDOW_LABEL: Record<MonitoringWindow, string> = {
  "1h": "Last 1 hour",
  "3h": "Last 3 hours",
  "8h": "Last 8 hours",
  "12h": "Last 12 hours",
  "24h": "Last 24 hours",
  "3d": "Last 3 days",
  "7d": "Last 7 days",
};

/** "5-minute", "2-hour": the bucket size, in words a chart subtitle can use. */
export function bucketLabel(seconds: number): string {
  return seconds < 3600 ? `${seconds / 60}-minute` : `${seconds / 3600}-hour`;
}

function clock(iso: string): string {
  return new Date(iso).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Presets, or the custom range a drag-to-zoom produced, with a way back. Says
 * what each bar is (the bucket size) and how fresh the data is, because a chart
 * that looks current but is minutes old invites the wrong conclusion.
 */
export function RangeControl({
  window,
  data,
  onWindow,
  onReset,
}: {
  window: MonitoringWindow | null;
  data: AgentMonitoring | undefined;
  onWindow: (w: MonitoringWindow) => void;
  onReset: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      {window ? (
        <Select
          value={window}
          onValueChange={(v) => onWindow(v as MonitoringWindow)}
        >
          <SelectTrigger className="h-8 w-44 text-xs" aria-label="time range">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {MONITORING_WINDOWS.map((w) => (
              <SelectItem key={w} value={w} className="text-xs">
                {WINDOW_LABEL[w]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <>
          <span className="rounded-md border border-[var(--border-subtle)] px-2.5 py-1.5 text-xs text-text-primary">
            {data
              ? `${clock(data.range_start)} – ${clock(data.range_end)}`
              : "Custom range"}
          </span>
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-xs"
            onClick={onReset}
          >
            <RotateCcw className="mr-1 size-3.5" />
            Reset zoom
          </Button>
        </>
      )}
      <p className="text-2xs text-text-tertiary" aria-live="polite">
        {data ? (
          <>
            {`${bucketLabel(data.bucket_seconds)} buckets`}
            {data.summary.resources_as_of &&
              ` · resources as of ${formatAbsoluteTimestamp(
                Date.parse(data.summary.resources_as_of),
              )}`}
            {" · drag across a chart to zoom, click a bar to list its queries"}
          </>
        ) : (
          "Loading…"
        )}
      </p>
    </div>
  );
}
