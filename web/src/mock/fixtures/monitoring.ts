import type {
  ActivityState,
  AgentMonitoring,
  MonitoringWindow,
} from "@/types/agent";

// window -> [span seconds, bucket seconds]. Mirrors the backend's WINDOWS map;
// tests/mock/contract.test.ts asserts the shape stays in step with it.
const WINDOW_SPEC: Record<MonitoringWindow, [number, number]> = {
  "1h": [3600, 60],
  "3h": [10800, 120],
  "8h": [28800, 300],
  "12h": [43200, 300],
  "24h": [86400, 600],
};

/**
 * A deterministic but shaped monitoring payload.
 *
 * Shaped rather than flat because a mock of all-zeros hides exactly the bugs
 * these charts have: axis domains, stacking order, gap handling for unmeasured
 * buckets, and the legend's per-series totals all look fine against a flat line.
 * The series are derived from the bucket index, so the same window always renders
 * the same picture and a screenshot diff means something.
 */
export function makeMonitoring(
  window: MonitoringWindow = "8h",
  now = Date.now(),
): AgentMonitoring {
  const [span, bucket] = WINDOW_SPEC[window];
  const bucketMs = bucket * 1000;
  const end = Math.ceil(now / bucketMs) * bucketMs;
  const start = end - span * 1000;
  const count = span / bucket;
  const at = (i: number) => new Date(start + i * bucketMs).toISOString();

  const peak_query_count = [];
  const completed_query_count = [];
  const activity = [];
  const utilization = [];
  const failures = [];

  for (let i = 0; i < count; i++) {
    // A busy stretch in the middle third, quiet either side.
    const busy = i > count * 0.35 && i < count * 0.6;
    const veryEarly = i < count * 0.08;

    const running = busy ? 1 + (i % 3) : i % 7 === 0 ? 1 : 0;
    const queued = busy && i % 4 === 0 ? 1 + (i % 2) : 0;
    peak_query_count.push({ t: at(i), running, queued });
    completed_query_count.push({
      t: at(i),
      per_minute: busy
        ? Number((1 + (i % 5) * 0.4).toFixed(2))
        : i % 3 === 0
          ? 0.2
          : 0,
    });

    let state: ActivityState = "ready";
    if (veryEarly) state = "starting";
    else if (busy || running || queued) state = "query";
    else if (i % 11 === 0) state = "other";
    activity.push({ t: at(i), state });

    // A run of unmeasured buckets, so the "gap not zero" rendering is exercised.
    const measured = !veryEarly && !(i > count * 0.8 && i < count * 0.85);
    utilization.push({
      t: at(i),
      cpu_avg: measured
        ? Number((busy ? 40 + (i % 20) : 6 + (i % 5)).toFixed(1))
        : null,
      cpu_max: measured
        ? Number((busy ? 70 + (i % 25) : 12 + (i % 8)).toFixed(1))
        : null,
      mem_avg: measured
        ? Number((busy ? 55 + (i % 10) : 20 + (i % 4)).toFixed(1))
        : null,
      mem_max: measured
        ? Number((busy ? 72 + (i % 12) : 26 + (i % 6)).toFixed(1))
        : null,
    });

    if (busy && i % 9 === 0) {
      failures.push({ t: at(i), reason: "queue_full", count: 1 });
    }
    if (busy && i % 14 === 0) {
      failures.push({ t: at(i), reason: "out_of_memory", count: 1 });
    }
  }

  const completed = completed_query_count.reduce(
    (sum, p) => sum + Math.round((p.per_minute * bucket) / 60),
    0,
  );
  const up: ActivityState[] = ["ready", "other", "query"];
  const upBuckets = activity.filter((p) => up.includes(p.state)).length;
  const busyBuckets = activity.filter((p) => p.state === "query").length;

  return {
    window,
    bucket_seconds: bucket,
    start: new Date(start).toISOString(),
    end: new Date(end).toISOString(),
    peak_query_count,
    completed_query_count,
    activity,
    failures,
    utilization,
    summary: {
      uptime_s: upBuckets * bucket,
      busy_ratio: upBuckets
        ? Number((busyBuckets / upBuckets).toFixed(3))
        : null,
      completed,
      failed: failures.reduce((sum, f) => sum + f.count, 0),
      idle_timeout_minutes: 20,
    },
  };
}

/** An agent that has never reported anything — the empty state. */
export function makeEmptyMonitoring(
  window: MonitoringWindow = "8h",
  now = Date.now(),
): AgentMonitoring {
  const base = makeMonitoring(window, now);
  return {
    ...base,
    peak_query_count: base.peak_query_count.map((p) => ({
      ...p,
      running: 0,
      queued: 0,
    })),
    completed_query_count: base.completed_query_count.map((p) => ({
      ...p,
      per_minute: 0,
    })),
    // "unknown", not "down": an agent older than the lifecycle trail has no
    // recorded history, which is a different claim from having been off.
    activity: base.activity.map((p) => ({ ...p, state: "unknown" as const })),
    failures: [],
    utilization: base.utilization.map((p) => ({
      ...p,
      cpu_avg: null,
      cpu_max: null,
      mem_avg: null,
      mem_max: null,
    })),
    summary: {
      uptime_s: 0,
      busy_ratio: null,
      completed: 0,
      failed: 0,
      idle_timeout_minutes: null,
    },
  };
}
