import type {
  AgentMonitoring,
  MonitoringBucket,
  MonitoringRange,
  MonitoringSpan,
  MonitoringWindow,
} from "@/types/agent";

// Mirrors the backend's presets and bucket choice (services/agent_monitoring.py);
// tests/mock/contract.test.ts asserts the two stay in step.
export const WINDOW_SPAN_S: Record<MonitoringWindow, number> = {
  "1h": 3600,
  "3h": 10800,
  "8h": 28800,
  "12h": 43200,
  "24h": 86400,
  "3d": 259200,
  "7d": 604800,
};
const BUCKET_STEPS_S = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600];

export function chooseBucket(spanS: number): number {
  return (
    BUCKET_STEPS_S.find((b) => Math.ceil(spanS / b) <= 150) ??
    BUCKET_STEPS_S[BUCKET_STEPS_S.length - 1]
  );
}

/**
 * A deterministic but shaped monitoring payload.
 *
 * Shaped rather than flat because a mock of all-zeros hides exactly the bugs
 * these charts have: stacking order, the partial last bucket, gap handling for
 * unmeasured buckets, down-time shading and the legends' totals all look fine
 * against a flat line. Series derive from the bucket index, so the same range
 * always renders the same picture.
 */
export function makeMonitoring(
  range: MonitoringRange = { window: "8h" },
  now = Date.now(),
): AgentMonitoring {
  const endMs = "window" in range ? now : Math.min(Date.parse(range.end), now);
  const startMs =
    "window" in range
      ? now - WINDOW_SPAN_S[range.window] * 1000
      : Date.parse(range.start);
  const bucketS = chooseBucket((endMs - startMs) / 1000);
  const bucketMs = bucketS * 1000;

  const edges = [startMs];
  for (
    let e = Math.floor(startMs / bucketMs) * bucketMs + bucketMs;
    e < endMs;
    e += bucketMs
  ) {
    edges.push(e);
  }
  const n = edges.length;
  // The agent came up a little way in and was stopped for a stretch near the end.
  const upFrom = edges[Math.floor(n * 0.08)];
  const downFrom = edges[Math.floor(n * 0.8)];
  const downTo = edges[Math.floor(n * 0.85)];

  const buckets: MonitoringBucket[] = edges.map((edge, i) => {
    const end = i + 1 < n ? edges[i + 1] : endMs;
    const seconds = (end - edge) / 1000;
    const starting = edge < upFrom;
    const down = edge >= downFrom && edge < downTo;
    const busy = i > n * 0.35 && i < n * 0.6;
    const up = !starting && !down;
    const busyS = up ? seconds * (busy ? 0.7 : i % 7 === 0 ? 0.1 : 0) : 0;
    const failed: Record<string, number> = {};
    if (busy && i % 9 === 0) failed.queue_full = 1;
    if (busy && i % 14 === 0) failed.out_of_memory = 1;
    if (i % 17 === 0 && up) failed.sql_error = 1;
    return {
      t: new Date(edge).toISOString(),
      seconds,
      partial: seconds < bucketS,
      busy_s: busyS,
      idle_s: up ? seconds - busyS : 0,
      starting_s: starting ? seconds : 0,
      down_s: down ? seconds : 0,
      unknown_s: 0,
      running_avg: up
        ? Number(((busy ? 1.4 : 0.1) + (i % 3) * 0.1).toFixed(3))
        : 0,
      queued_avg: busy && i % 4 === 0 ? 0.4 : 0,
      compute_wait_avg: starting && i === 0 ? 0.2 : 0,
      peak_running: up ? (busy ? 2 + (i % 3) : i % 7 === 0 ? 1 : 0) : 0,
      done: up ? (busy ? 3 + (i % 5) : i % 3 === 0 ? 1 : 0) : 0,
      cancelled: busy && i % 11 === 0 ? 1 : 0,
      failed,
      wait_p95_ms: busy ? 800 + (i % 4) * 400 : up && i % 3 === 0 ? 40 : null,
      wait_n: busy ? 4 : up && i % 3 === 0 ? 1 : 0,
      cpu_avg: up
        ? Number((busy ? 40 + (i % 20) : 6 + (i % 5)).toFixed(1))
        : null,
      cpu_max: up
        ? Number((busy ? 70 + (i % 25) : 12 + (i % 8)).toFixed(1))
        : null,
      mem_avg: up
        ? Number((busy ? 55 + (i % 10) : 20 + (i % 4)).toFixed(1))
        : null,
      mem_max: up
        ? Number((busy ? 72 + (i % 12) : 26 + (i % 6)).toFixed(1))
        : null,
      oom_kills: up ? (busy && i % 14 === 0 ? 1 : 0) : null,
      coverage: up ? 1 : null,
    };
  });

  const spans: MonitoringSpan[] = [
    {
      start: new Date(startMs).toISOString(),
      end: new Date(upFrom).toISOString(),
      state: "starting",
    },
    {
      start: new Date(upFrom).toISOString(),
      end: new Date(downFrom).toISOString(),
      state: "up",
    },
    {
      start: new Date(downFrom).toISOString(),
      end: new Date(downTo).toISOString(),
      state: "down",
    },
    {
      start: new Date(downTo).toISOString(),
      end: new Date(endMs).toISOString(),
      state: "up",
    },
  ].filter((s) => s.start < s.end) as MonitoringSpan[];

  const failedByReason: Record<string, number> = {};
  for (const b of buckets) {
    for (const [reason, count] of Object.entries(b.failed)) {
      failedByReason[reason] = (failedByReason[reason] ?? 0) + count;
    }
  }
  const total = (pick: (b: MonitoringBucket) => number) =>
    buckets.reduce((sum, b) => sum + pick(b), 0);
  const uptime = total((b) => b.busy_s + b.idle_s);
  const busyTotal = total((b) => b.busy_s);
  const failed = Object.values(failedByReason).reduce((a, b) => a + b, 0);
  const cancelled = total((b) => b.cancelled);
  const measured = buckets.filter((b) => b.cpu_max != null);

  return {
    preset: "window" in range ? range.window : null,
    range_start: new Date(startMs).toISOString(),
    range_end: new Date(endMs).toISOString(),
    bucket_seconds: bucketS,
    generated_at: new Date(now).toISOString(),
    buckets,
    spans,
    summary: {
      uptime_s: Math.round(uptime),
      busy_s: Math.round(busyTotal),
      idle_s: Math.round(uptime - busyTotal),
      busy_ratio: uptime ? Number((busyTotal / uptime).toFixed(3)) : null,
      finished: total((b) => b.done) + cancelled + failed,
      failed,
      cancelled,
      failed_by_reason: failedByReason,
      wait_p95_ms: 1600,
      wait_n: total((b) => b.wait_n),
      peak_running: Math.max(0, ...buckets.map((b) => b.peak_running)),
      cpu_peak: measured.length
        ? Math.max(...measured.map((b) => b.cpu_max!))
        : null,
      mem_peak: measured.length
        ? Math.max(...measured.map((b) => b.mem_max!))
        : null,
      resources_as_of: new Date(now).toISOString(),
    },
  };
}

/** An agent with no lifecycle record and nothing reported — the empty state. */
export function makeEmptyMonitoring(
  range: MonitoringRange = { window: "8h" },
  now = Date.now(),
): AgentMonitoring {
  const base = makeMonitoring(range, now);
  return {
    ...base,
    // "No record", not "down": an agent older than the lifecycle trail has no
    // recorded history, which is a different claim from having been off.
    spans: [{ start: base.range_start, end: base.range_end, state: "unknown" }],
    buckets: base.buckets.map((b) => ({
      ...b,
      busy_s: 0,
      idle_s: 0,
      starting_s: 0,
      down_s: 0,
      unknown_s: b.seconds,
      running_avg: 0,
      queued_avg: 0,
      compute_wait_avg: 0,
      peak_running: 0,
      done: 0,
      cancelled: 0,
      failed: {},
      wait_p95_ms: null,
      wait_n: 0,
      cpu_avg: null,
      cpu_max: null,
      mem_avg: null,
      mem_max: null,
      oom_kills: null,
      coverage: null,
    })),
    summary: {
      uptime_s: 0,
      busy_s: 0,
      idle_s: 0,
      busy_ratio: null,
      finished: 0,
      failed: 0,
      cancelled: 0,
      failed_by_reason: {},
      wait_p95_ms: null,
      wait_n: 0,
      peak_running: 0,
      cpu_peak: null,
      mem_peak: null,
      resources_as_of: null,
    },
  };
}
