"""Aggregate one agent's telemetry into the series the monitoring page draws.

Every series shares one bucket grid so the charts line up vertically and a single
time range governs all of them — the property that makes a stack of charts read as
one story rather than five unrelated pictures.

**Exact where the data allows it.** Anything with a start and an end is computed from
its exact interval rather than from samples:

* *Lifecycle* (up / starting / down / unknown) replays the agent's lifecycle trail
  into exact spans.
* *Query activity* comes from the ``queries`` rows themselves. A one-shot query's
  ``running_at`` is stamped when the agent reports it past admission, and a session
  statement's when it starts executing, so ``started_at → running_at`` is the time
  it waited (in the agent's admission queue, or for compute that was still
  starting) and ``running_at → finished_at`` the time it ran. From those intervals:
  average concurrency per bucket (Little's law: query-seconds ÷ bucket seconds),
  the true peak (a sweep line), and busy time (the measure of their union within
  the time the agent was up). None of it depends on the bucket size, so the same
  history reads the same at every zoom level; the old bucket-flag "busy %" read
  anywhere from 0 % to 67 % for one agent.
* *Resources* (CPU, memory) can only be sampled; they come from the per-minute
  rollup, plus the agent's live samples for the minute not yet written, so the
  right-hand edge of the charts is seconds old rather than a minute.

Aggregation happens in Python rather than in ``GROUP BY date_trunc(...)`` so the
unit suite (SQLite) runs the same code path as production.
"""

from __future__ import annotations

import bisect
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import Float
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement

from api.config import settings
from api.models.agent import Agent, AgentLifecycleEvent, AgentMetricsMinute
from api.models.query import Query
from api.services.agent_dispatch import agent_recent_samples, is_agent_connected
from api.services.agent_telemetry import MinuteAccumulator, accumulate_into
from api.services.intervals import Interval, measure, merge, nearest_rank, spread, sweep
from api.services.query_failure import classify_failure
from api.services.query_history import HIDDEN_ORIGINS

PRESETS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "3h": timedelta(hours=3),
    "8h": timedelta(hours=8),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
    "3d": timedelta(days=3),
    "7d": timedelta(days=7),
}
DEFAULT_WINDOW = "8h"
# A custom range narrower than this has too few buckets to show a shape.
MIN_RANGE = timedelta(minutes=5)

# Candidate bucket sizes, smallest first. The chosen one is the smallest that keeps a
# range to at most MAX_BUCKETS points: enough to show shape, few enough to read. It
# reproduces the old fixed table (1h → 1 min … 24h → 10 min) and gives 3d → 30 min
# and 7d → 2 h.
_BUCKET_STEPS = [timedelta(minutes=m) for m in (1, 2, 5, 10, 15, 30, 60, 120, 180, 360)]
MAX_BUCKETS = 150

# Lifecycle states. "unknown" means no lifecycle record covers the time — an agent
# older than the trail — which is deliberately distinct from "down".
STATE_UP = "up"
STATE_STARTING = "starting"
STATE_DOWN = "down"
STATE_UNKNOWN = "unknown"

_EVENT_STATE = {
    "provisioning": STATE_STARTING,
    "connected": STATE_UP,
    "disconnected": STATE_DOWN,
    "terminating": STATE_DOWN,
    "terminated": STATE_DOWN,
    "failed": STATE_DOWN,
}

_TERMINAL = ("done", "failed", "cancelled")


def choose_bucket(span: timedelta) -> timedelta:
    for step in _BUCKET_STEPS:
        if math.ceil(span / step) <= MAX_BUCKETS:
            return step
    return _BUCKET_STEPS[-1]


@dataclass(frozen=True)
class Grid:
    """The shared bucket grid every series is projected onto.

    Interior edges are aligned to the bucket size rather than to "now", so the
    x-axis is stable as the page polls. The grid covers exactly the requested range:
    the first bucket starts at ``start`` and the last ends at ``end`` (never in the
    future), so either may be shorter than a full bucket.
    """

    edges: list[datetime]
    end: datetime
    bucket: timedelta

    @property
    def start(self) -> datetime:
        return self.edges[0]

    def seconds(self, i: int) -> float:
        bucket_end = self.edges[i + 1] if i + 1 < len(self.edges) else self.end
        return (bucket_end - self.edges[i]).total_seconds()

    def index_of(self, at: datetime) -> int | None:
        if at < self.start or at >= self.end:
            return None
        return bisect.bisect_right(self.edges, at) - 1

    def epoch_edges(self) -> list[float]:
        return [e.timestamp() for e in self.edges]


def build_grid(start: datetime, end: datetime) -> Grid:
    bucket = choose_bucket(end - start)
    bucket_s = int(bucket.total_seconds())
    aligned = datetime.fromtimestamp(int(start.timestamp()) // bucket_s * bucket_s, tz=UTC)
    edges: list[datetime] = [start]
    edge = aligned + bucket
    while edge < end:
        edges.append(edge)
        edge += bucket
    return Grid(edges=edges, end=end, bucket=bucket)


def resolve_range(
    window: str | None,
    start: datetime | None,
    end: datetime | None,
    now: datetime | None = None,
) -> tuple[str | None, datetime, datetime]:
    """(preset, start, end) for a request. The caller has already validated the
    combination; this clamps the end to now and the start to retention."""
    now = now or datetime.now(tz=UTC)
    if start is None or end is None:
        preset = window or DEFAULT_WINDOW
        return preset, now - PRESETS[preset], now
    end = min(_aware(end), now)
    oldest = now - timedelta(hours=settings.agent_metrics_retention_hours)
    return None, max(_aware(start), oldest), end


def _aware(value: datetime) -> datetime:
    """Treat a naive timestamp (SQLite under tests) as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _ts(value: datetime) -> float:
    # Hot: called for every timestamp of every query in the range.
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


# ── Loading ──────────────────────────────────────────────────────────────────


class _Epoch(FunctionElement):
    """A timestamp as float epoch seconds, NULL-preserving.

    Spelled per dialect: Postgres has ``EXTRACT(EPOCH ...)``, SQLite (the unit-test
    database, storing UTC text) goes through ``julianday``, to the millisecond.
    """

    inherit_cache = True
    type = Float()


@compiles(_Epoch, "postgresql")
def _epoch_pg(element, compiler, **kw) -> str:
    (value,) = list(element.clauses)
    return f"CAST(EXTRACT(EPOCH FROM {compiler.process(value, **kw)}) AS DOUBLE PRECISION)"


@compiles(_Epoch, "sqlite")
@compiles(_Epoch)
def _epoch_sqlite(element, compiler, **kw) -> str:
    (value,) = list(element.clauses)
    # julianday's day fraction is not exact in binary; rounding to the millisecond
    # keeps a timestamp on a bucket edge from landing in the bucket before it.
    return f"ROUND((julianday({compiler.process(value, **kw)}) - 2440587.5) * 86400.0, 3)"


async def _load_rollup(db: AsyncSession, agent_id: uuid.UUID, grid: Grid) -> list[sa.Row]:
    t = AgentMetricsMinute
    return list(
        (
            await db.execute(
                sa.select(
                    t.minute,
                    t.cpu_avg,
                    t.cpu_max,
                    t.mem_avg,
                    t.mem_max,
                    t.sample_count,
                    t.covered_s,
                    t.oom_kills,
                )
                .where(t.agent_id == agent_id, t.minute >= grid.start, t.minute < grid.end)
                .order_by(t.minute)
            )
        ).all()
    )


async def _load_queries(db: AsyncSession, agent_id: uuid.UUID, grid: Grid) -> list[sa.Row]:
    """Every visible query whose life overlaps the range, finished or not.

    Includes runs parked for this agent while it was still starting (``agent_id``
    is only set once one is bound), so the time they waited for compute shows.
    The error text is only fetched where it is used, for failed rows, and the
    timestamps arrive as epoch seconds: a busy agent's week is hundreds of thousands
    of rows, and building a datetime for each of them was a third of the page's cost.
    """

    def branch(owner_clause: sa.ColumnElement[bool]) -> sa.Select:
        return sa.select(
            _Epoch(Query.started_at),
            _Epoch(Query.running_at),
            _Epoch(Query.finished_at),
            Query.status,
            sa.case((Query.status == "failed", Query.error), else_=None).label("error"),
        ).where(
            owner_clause,
            Query.started_at < grid.end,
            sa.or_(Query.finished_at.is_(None), Query.finished_at >= grid.start),
            sa.or_(Query.origin.is_(None), Query.origin.not_in(HIDDEN_ORIGINS)),
        )

    stmt = sa.union_all(
        branch(Query.agent_id == agent_id),
        branch(sa.and_(Query.agent_id.is_(None), Query.requested_agent_id == agent_id)),
    )
    return list((await db.execute(stmt)).all())


async def _load_events(
    db: AsyncSession, agent_id: uuid.UUID, grid: Grid
) -> tuple[str, list[AgentLifecycleEvent]]:
    """Events inside the range, plus the state the agent was already in at its start.

    The seed matters more than the events: an agent that has been quietly connected
    for a week has no events *in* an 8-hour range, and without the preceding one the
    whole timeline would render as unknown.
    """
    prior = (
        await db.execute(
            sa.select(AgentLifecycleEvent.event)
            .where(AgentLifecycleEvent.agent_id == agent_id, AgentLifecycleEvent.at < grid.start)
            .order_by(AgentLifecycleEvent.at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    inside = list(
        (
            await db.execute(
                sa.select(AgentLifecycleEvent)
                .where(
                    AgentLifecycleEvent.agent_id == agent_id,
                    AgentLifecycleEvent.at >= grid.start,
                    AgentLifecycleEvent.at < grid.end,
                )
                .order_by(AgentLifecycleEvent.at)
            )
        )
        .scalars()
        .all()
    )
    seed = _EVENT_STATE.get(prior, STATE_UNKNOWN) if prior else STATE_UNKNOWN
    return seed, inside


# ── Lifecycle spans ──────────────────────────────────────────────────────────


def _spans(
    grid: Grid,
    seed: str,
    events: list[AgentLifecycleEvent],
    *,
    present: bool,
    last_ping_at: datetime | None,
) -> list[tuple[float, float, str]]:
    """Exact (start, end, state) spans covering the grid, in epoch seconds.

    If the trail says the agent is still up but it is not actually present, the up
    span ends at its last proof of life. The presence sweeper will record that
    shortly; this keeps the page right in the meantime.
    """
    spans: list[tuple[float, float, str]] = []
    current, cursor = seed, grid.start.timestamp()
    for event in events:
        at = max(_ts(event.at), grid.start.timestamp())
        if at > cursor:
            spans.append((cursor, at, current))
        current = _EVENT_STATE.get(event.event, current)
        cursor = max(cursor, at)
    end = grid.end.timestamp()
    if current == STATE_UP and not present and last_ping_at is not None:
        lost = min(max(_ts(last_ping_at), cursor), end)
        if lost > cursor:
            spans.append((cursor, lost, current))
        current, cursor = STATE_DOWN, lost
    if end > cursor:
        spans.append((cursor, end, current))
    return spans


def _of_state(spans: list[tuple[float, float, str]], *states: str) -> list[Interval]:
    return merge((s, e) for s, e, state in spans if state in states)


# ── Resources ────────────────────────────────────────────────────────────────


def _live_minutes(samples: list[dict], after: datetime | None) -> list[MinuteAccumulator]:
    """Fold live samples into minutes not yet written to the rollup."""
    minutes: dict[datetime, MinuteAccumulator] = {}
    for sample in samples:
        accumulate_into(minutes, sample)
    return [acc for minute, acc in sorted(minutes.items()) if after is None or minute > after]


_RESOURCE_KEYS = ("cpu_avg", "cpu_max", "mem_avg", "mem_max", "oom_kills", "coverage")


def _resource_buckets(grid: Grid, rows: list, live: list[MinuteAccumulator]) -> list[dict]:
    per: dict[int, dict] = {}

    def fold(
        minute: datetime,
        cpu_avg: float,
        cpu_max: float,
        mem_avg: float,
        mem_max: float,
        count: int,
        covered: float | None,
        ooms: int | None,
    ) -> None:
        idx = grid.index_of(_aware(minute))
        if idx is None or not count:
            return
        acc = per.setdefault(
            idx,
            {"cpu_w": 0.0, "mem_w": 0.0, "n": 0, "cpu_max": 0.0, "mem_max": 0.0},
        )
        acc["cpu_w"] += cpu_avg * count
        acc["mem_w"] += mem_avg * count
        acc["n"] += count
        acc["cpu_max"] = max(acc["cpu_max"], cpu_max)
        acc["mem_max"] = max(acc["mem_max"], mem_max)
        if covered is not None:
            acc["covered"] = acc.get("covered", 0.0) + covered
        if ooms is not None:
            acc["oom"] = acc.get("oom", 0) + ooms

    for r in rows:
        fold(
            r.minute,
            r.cpu_avg,
            r.cpu_max,
            r.mem_avg,
            r.mem_max,
            r.sample_count,
            r.covered_s,
            r.oom_kills,
        )
    for a in live:
        fold(
            a.minute,
            a.cpu_sum / a.count if a.count else 0.0,
            a.cpu_max,
            a.mem_sum / a.count if a.count else 0.0,
            a.mem_max,
            a.count,
            a.covered_s,
            a.oom_kills,
        )

    out = []
    for i in range(len(grid.edges)):
        acc = per.get(i)
        if not acc:
            # Not measured: null, so the chart draws a gap rather than claiming 0 %.
            out.append(dict.fromkeys(_RESOURCE_KEYS))
            continue
        covered = acc.get("covered")
        out.append(
            {
                "cpu_avg": round(acc["cpu_w"] / acc["n"], 2),
                "cpu_max": round(acc["cpu_max"], 2),
                "mem_avg": round(acc["mem_w"] / acc["n"], 2),
                "mem_max": round(acc["mem_max"], 2),
                "oom_kills": acc.get("oom"),
                "coverage": (
                    round(min(1.0, covered / grid.seconds(i)), 3) if covered is not None else None
                ),
            }
        )
    return out


# ── Assembly ─────────────────────────────────────────────────────────────────


async def build_monitoring(
    db: AsyncSession,
    agent: Agent,
    *,
    window: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    now: datetime | None = None,
) -> dict:
    """Every series for one agent over one range, on a shared bucket grid."""
    now = now or datetime.now(tz=UTC)
    preset, range_start, range_end = resolve_range(window, start, end, now)
    grid = build_grid(range_start, range_end)
    edges = grid.epoch_edges()
    end_s = grid.end.timestamp()
    n = len(edges)

    rows = await _load_rollup(db, agent.id, grid)
    live = _live_minutes(
        await agent_recent_samples(db, agent.id) if range_end >= now - timedelta(minutes=2) else [],
        _aware(rows[-1].minute) if rows else None,
    )
    resources = _resource_buckets(grid, rows, live)
    queries = await _load_queries(db, agent.id, grid)
    seed, events = await _load_events(db, agent.id, grid)
    present = await is_agent_connected(db, agent.id)
    spans = _spans(grid, seed, events, present=present, last_ping_at=agent.last_ping_at)

    up = _of_state(spans, STATE_UP)
    unknown = _of_state(spans, STATE_UNKNOWN)
    starting = _of_state(spans, STATE_STARTING)
    down = _of_state(spans, STATE_DOWN)
    # Where the trail can't say, the agent can still be *seen* to have been up.
    reachable = merge([*up, *unknown])
    compute_absent = merge([*starting, *down])

    runs: list[Interval] = []
    waits: list[Interval] = []
    wait_ended: list[tuple[float, float]] = []  # (when the wait ended, wait seconds)
    done = [0] * n
    cancelled = [0] * n
    failed: list[dict[str, int]] = [defaultdict(int) for _ in range(n)]
    for started, ran, finished, status, error in queries:
        wait_end = ran if ran is not None else (finished if finished is not None else end_s)
        waits.append((started, max(started, wait_end)))
        if ran is not None:
            runs.append((ran, finished if finished is not None else end_s))
            wait_ended.append((ran, max(0.0, ran - started)))
        if finished is not None and status in _TERMINAL and edges[0] <= finished < end_s:
            idx = bisect.bisect_right(edges, finished) - 1
            if idx >= 0:
                if status == "done":
                    done[idx] += 1
                elif status == "cancelled":
                    cancelled[idx] += 1
                else:
                    failed[idx][classify_failure(error)] += 1

    # Only time the agent was reachable counts as running: a row still marked
    # running after the agent went away stops counting where the agent stopped.
    run_sweep = sweep(runs, reachable, edges, end_s, widen_instants=True)
    running_s, busy_s, peaks = run_sweep.seconds, run_sweep.covered, run_sweep.peak
    busy_unknown_s = sweep(runs, unknown, edges, end_s, widen_instants=True).covered
    queued_s = sweep(waits, reachable, edges, end_s).seconds
    compute_wait_s = sweep(waits, compute_absent, edges, end_s).seconds
    up_s = spread(up, edges, end_s)
    unknown_s = spread(unknown, edges, end_s)
    starting_s = spread(starting, edges, end_s)
    down_s = spread(down, edges, end_s)

    waits_by_bucket: list[list[float]] = [[] for _ in range(n)]
    for ended, seconds in wait_ended:
        if edges[0] <= ended < end_s:
            waits_by_bucket[bisect.bisect_right(edges, ended) - 1].append(seconds)

    buckets = []
    for i, edge in enumerate(grid.edges):
        length = grid.seconds(i)
        bucket_wait = nearest_rank(waits_by_bucket[i], 0.95)
        buckets.append(
            {
                "t": edge,
                "seconds": round(length, 3),
                "partial": length < grid.bucket.total_seconds(),
                # Where the agent's time went; these sum to the bucket length.
                "busy_s": round(busy_s[i], 3),
                "idle_s": round(max(0.0, up_s[i] - (busy_s[i] - busy_unknown_s[i])), 3),
                "starting_s": round(starting_s[i], 3),
                "down_s": round(down_s[i], 3),
                "unknown_s": round(max(0.0, unknown_s[i] - busy_unknown_s[i]), 3),
                # Average number of queries in each state over the bucket.
                "running_avg": round(running_s[i] / length, 3) if length else 0.0,
                "queued_avg": round(queued_s[i] / length, 3) if length else 0.0,
                "compute_wait_avg": round(compute_wait_s[i] / length, 3) if length else 0.0,
                "peak_running": peaks[i],
                "done": done[i],
                "cancelled": cancelled[i],
                "failed": dict(sorted(failed[i].items())),
                "wait_p95_ms": round(bucket_wait * 1000) if bucket_wait is not None else None,
                "wait_n": len(waits_by_bucket[i]),
                **resources[i],
            }
        )

    uptime_s = measure(up) + sum(busy_unknown_s)
    busy_total = sum(busy_s)
    failed_by_reason: dict[str, int] = defaultdict(int)
    for bucket_failures in failed:
        for reason, count in bucket_failures.items():
            failed_by_reason[reason] += count
    all_waits = [w for bucket_waits in waits_by_bucket for w in bucket_waits]
    window_wait = nearest_rank(all_waits, 0.95)
    measured_cpu = [b["cpu_max"] for b in buckets if b["cpu_max"] is not None]
    measured_mem = [b["mem_max"] for b in buckets if b["mem_max"] is not None]
    newest_rollup = _aware(rows[-1].minute) + timedelta(minutes=1) if rows else None
    newest_live = max((_live_as_of(a) for a in live), default=None)

    return {
        "preset": preset,
        "range_start": range_start,
        "range_end": range_end,
        "bucket_seconds": int(grid.bucket.total_seconds()),
        "generated_at": now,
        "buckets": buckets,
        "spans": [{"start": _dt(s), "end": _dt(e), "state": st} for s, e, st in spans],
        "summary": {
            "uptime_s": round(uptime_s),
            "busy_s": round(busy_total),
            "idle_s": round(max(0.0, uptime_s - busy_total)),
            # Share of up time with at least one query running. None when the agent
            # was never up: a ratio there would be a division by zero dressed as 0 %.
            "busy_ratio": round(busy_total / uptime_s, 3) if uptime_s else None,
            "finished": sum(done) + sum(cancelled) + sum(failed_by_reason.values()),
            "failed": sum(failed_by_reason.values()),
            "cancelled": sum(cancelled),
            "failed_by_reason": dict(sorted(failed_by_reason.items())),
            "wait_p95_ms": round(window_wait * 1000) if window_wait is not None else None,
            "wait_n": len(all_waits),
            "peak_running": max(peaks, default=0),
            "cpu_peak": max(measured_cpu, default=None),
            "mem_peak": max(measured_mem, default=None),
            "resources_as_of": max(
                (t for t in (newest_rollup, newest_live) if t is not None), default=None
            ),
        },
    }


def _live_as_of(acc: MinuteAccumulator) -> datetime:
    return acc.last_sampled_at or acc.minute


def _dt(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)
