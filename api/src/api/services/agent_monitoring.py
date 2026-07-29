"""Aggregate one agent's telemetry into the series the monitoring page draws.

Every series shares one bucket grid so the charts line up vertically and a single
time filter governs all of them — the property that makes a stack of charts read as
one story rather than five unrelated pictures.

**Why the bucketing happens in Python.** The obvious ``GROUP BY date_trunc(...)``
does not compile on SQLite, and the unit suite runs entirely on
``sqlite+aiosqlite:///:memory:``. Aggregating in the API keeps one code path under
test and in production. It costs a narrow-column scan of at most a day of rows,
served by ``ix_queries_agent_finished``; the rollup side is minute-grained and so is
bounded at 1,440 rows a day however busy the agent was.

**Why peak concurrency comes from the rollup, not from query timestamps.** The agent
holds queries in its own admission deque, and a query waiting there is queued in a
way no control-plane timestamp records. Reconstructing concurrency from
``running_at``/``finished_at`` would therefore undercount exactly the saturation an
operator is looking for. The rollup carries the agent's own reported depth, so the
chart is a max over each window rather than an exact interval sweep.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.agent import Agent, AgentLifecycleEvent, AgentMetricsMinute
from api.models.query import Query
from api.services.query_failure import classify_failure

# window -> (span, bucket). Bucket sizes keep every chart between 60 and 144 points:
# enough to show shape, few enough to stay legible and to render without thinning.
# A single fixed bucket cannot do that across the range: five minutes would leave a
# 1-hour window just 12 points, and a 24-hour window nearly 300.
WINDOWS: dict[str, tuple[timedelta, timedelta]] = {
    "1h": (timedelta(hours=1), timedelta(minutes=1)),
    "3h": (timedelta(hours=3), timedelta(minutes=2)),
    "8h": (timedelta(hours=8), timedelta(minutes=5)),
    "12h": (timedelta(hours=12), timedelta(minutes=5)),
    "24h": (timedelta(hours=24), timedelta(minutes=10)),
}
DEFAULT_WINDOW = "8h"

# Origins that are machinery rather than someone's work. Excluded to match the
# history list (routers.queries.list_workspace_queries), so the count under the
# chart and the rows in the table can never disagree.
_HIDDEN_ORIGINS = ("sample", "metadata")

_TERMINAL = ("done", "failed", "cancelled")
_FAILED = ("failed", "cancelled")

# What the agent was doing during a bucket, most to least significant. A bucket in
# which anything ran is "query" even if the agent was mostly idle within it —
# the chart answers "was this agent earning its keep", and a burst says yes.
ACTIVITY_DOWN = "down"
ACTIVITY_STARTING = "starting"
ACTIVITY_QUERY = "query"
ACTIVITY_OTHER = "other"
ACTIVITY_READY = "ready"
# No lifecycle trail covers this bucket. Distinct from "down" on purpose: an agent
# that predates the trail has no recorded history, and drawing that as downtime
# would invent an outage that never happened.
ACTIVITY_UNKNOWN = "unknown"

# Lifecycle event -> the connectivity state it puts the agent into.
_EVENT_STATE = {
    "provisioning": ACTIVITY_STARTING,
    "connected": ACTIVITY_READY,
    "disconnected": ACTIVITY_DOWN,
    "terminating": ACTIVITY_DOWN,
    "terminated": ACTIVITY_DOWN,
    "failed": ACTIVITY_DOWN,
}


@dataclass(frozen=True)
class Grid:
    """The shared bucket grid every series is projected onto."""

    start: datetime
    end: datetime
    bucket: timedelta
    edges: list[datetime]

    def index_of(self, at: datetime) -> int | None:
        """Which bucket a timestamp falls in, or None if outside the window."""
        if at < self.start or at >= self.end:
            return None
        return int((at - self.start) / self.bucket)

    @property
    def count(self) -> int:
        return len(self.edges)


def build_grid(window: str, now: datetime | None = None) -> Grid:
    """Bucket edges for ``window``, aligned to the bucket size.

    Aligning to the bucket rather than to "now" keeps the x-axis stable as the page
    polls: without it every refresh shifts every bucket by a few seconds and the
    bars visibly jitter.
    """
    span, bucket = WINDOWS[window]
    now = now or datetime.now(tz=UTC)
    bucket_s = int(bucket.total_seconds())
    aligned = datetime.fromtimestamp((int(now.timestamp()) // bucket_s + 1) * bucket_s, tz=UTC)
    start = aligned - span
    edges = [start + bucket * i for i in range(int(span / bucket))]
    return Grid(start=start, end=aligned, bucket=bucket, edges=edges)


def _aware(value: datetime) -> datetime:
    """Treat a naive timestamp (SQLite under tests) as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _load_rollup(
    db: AsyncSession, agent_id: uuid.UUID, grid: Grid
) -> list[AgentMetricsMinute]:
    return list(
        (
            await db.execute(
                sa.select(AgentMetricsMinute)
                .where(
                    AgentMetricsMinute.agent_id == agent_id,
                    AgentMetricsMinute.minute >= grid.start,
                    AgentMetricsMinute.minute < grid.end,
                )
                .order_by(AgentMetricsMinute.minute)
            )
        )
        .scalars()
        .all()
    )


async def _load_queries(db: AsyncSession, agent_id: uuid.UUID, grid: Grid) -> list[sa.Row]:
    """Only the columns the charts read, over the window's finished runs."""
    return list(
        (
            await db.execute(
                sa.select(Query.finished_at, Query.status, Query.error)
                .where(
                    Query.agent_id == agent_id,
                    Query.finished_at.is_not(None),
                    Query.finished_at >= grid.start,
                    Query.finished_at < grid.end,
                    Query.status.in_(_TERMINAL),
                    sa.or_(Query.origin.is_(None), Query.origin.not_in(_HIDDEN_ORIGINS)),
                )
                .order_by(Query.finished_at)
            )
        ).all()
    )


async def _load_events(
    db: AsyncSession, agent_id: uuid.UUID, grid: Grid
) -> tuple[str, list[AgentLifecycleEvent]]:
    """Events inside the window, plus the state the agent was already in at its start.

    The seed matters more than the events: an agent that has been quietly connected
    for a week has no events *in* an 8-hour window, and without the preceding one
    the whole timeline would render as unknown.
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
    seed = _EVENT_STATE.get(prior, ACTIVITY_UNKNOWN) if prior else ACTIVITY_UNKNOWN
    return seed, inside


def _connectivity_per_bucket(
    grid: Grid, seed: str, events: list[AgentLifecycleEvent]
) -> tuple[list[str], float]:
    """Per-bucket connectivity, and total seconds spent connected in the window.

    A bucket takes the state that covered most of it, rather than the state at its
    leading edge: an agent that comes up two seconds into a ten-minute bucket was
    up for that bucket in every sense a reader cares about.
    """
    # (state, start, end) spans covering the whole window.
    spans: list[tuple[str, datetime, datetime]] = []
    current, cursor = seed, grid.start
    for event in events:
        at = max(_aware(event.at), grid.start)
        if at > cursor:
            spans.append((current, cursor, at))
        current = _EVENT_STATE.get(event.event, current)
        cursor = at
    spans.append((current, cursor, grid.end))

    per_bucket: list[str] = []
    uptime_s = 0.0
    for edge in grid.edges:
        bucket_end = edge + grid.bucket
        overlap: dict[str, float] = defaultdict(float)
        for state, span_start, span_end in spans:
            covered = (min(bucket_end, span_end) - max(edge, span_start)).total_seconds()
            if covered > 0:
                overlap[state] += covered
                if state == ACTIVITY_READY:
                    uptime_s += covered
        per_bucket.append(max(overlap, key=overlap.get) if overlap else ACTIVITY_UNKNOWN)
    return per_bucket, uptime_s


def _bucketed_rollup(grid: Grid, rows: list[AgentMetricsMinute]) -> dict[int, dict]:
    """Fold minute rows into buckets: max for peaks, sample-weighted mean for averages."""
    out: dict[int, dict] = {}
    for row in rows:
        idx = grid.index_of(_aware(row.minute))
        if idx is None:
            continue
        acc = out.setdefault(
            idx,
            {
                "running": 0,
                "queued": 0,
                "sessions": 0,
                "cpu_max": 0.0,
                "mem_max": 0.0,
                "cpu_weighted": 0.0,
                "mem_weighted": 0.0,
                "samples": 0,
            },
        )
        acc["running"] = max(acc["running"], row.running_max)
        acc["queued"] = max(acc["queued"], row.queued_max)
        acc["sessions"] = max(acc["sessions"], row.session_max)
        acc["cpu_max"] = max(acc["cpu_max"], row.cpu_max)
        acc["mem_max"] = max(acc["mem_max"], row.mem_max)
        acc["cpu_weighted"] += row.cpu_avg * row.sample_count
        acc["mem_weighted"] += row.mem_avg * row.sample_count
        acc["samples"] += row.sample_count
    return out


def _utilization_point(edge: datetime, metrics: dict | None) -> dict:
    """One CPU/memory point, all-null for a bucket the agent reported nothing in.

    Nulls rather than zeros: a gap in the line says "not measured", where a zero
    would claim the agent sat at 0% CPU during an outage it was not even up for.
    """
    if not metrics or not metrics["samples"]:
        return {"t": edge, "cpu_avg": None, "cpu_max": None, "mem_avg": None, "mem_max": None}
    return {
        "t": edge,
        "cpu_avg": round(metrics["cpu_weighted"] / metrics["samples"], 2),
        "cpu_max": round(metrics["cpu_max"], 2),
        "mem_avg": round(metrics["mem_weighted"] / metrics["samples"], 2),
        "mem_max": round(metrics["mem_max"], 2),
    }


async def build_monitoring(db: AsyncSession, agent: Agent, window: str) -> dict:
    """Every series for one agent over one window, on a shared bucket grid."""
    grid = build_grid(window)
    rollup = _bucketed_rollup(grid, await _load_rollup(db, agent.id, grid))
    queries = await _load_queries(db, agent.id, grid)
    seed, events = await _load_events(db, agent.id, grid)
    connectivity, uptime_s = _connectivity_per_bucket(grid, seed, events)

    completed = [0] * grid.count
    failures: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    failed_total = 0
    for finished_at, status, error in queries:
        idx = grid.index_of(_aware(finished_at))
        if idx is None:
            continue
        completed[idx] += 1
        if status in _FAILED:
            failed_total += 1
            reason = "cancelled" if status == "cancelled" else classify_failure(error)
            failures[idx][reason] += 1

    bucket_minutes = grid.bucket.total_seconds() / 60
    bucket_s = grid.bucket.total_seconds()

    # Refine connectivity into what the agent was actually doing. Only a bucket the
    # agent was up for can be busy; "starting" and "down" stay as they are.
    activity: list[dict] = []
    busy_buckets = 0
    up_buckets = 0
    for idx, edge in enumerate(grid.edges):
        state = connectivity[idx]
        metrics = rollup.get(idx)
        # An agent that reported telemetry, or finished a query, was demonstrably
        # up — whatever the lifecycle trail does or doesn't say. Without this an
        # agent older than the trail reads as "no data" for its whole history even
        # though we hold minute-by-minute proof it was working, which is a
        # different kind of wrong from claiming an outage.
        if state == ACTIVITY_UNKNOWN and (metrics or completed[idx]):
            state = ACTIVITY_READY
            uptime_s += bucket_s
        if state == ACTIVITY_READY:
            up_buckets += 1
            if completed[idx] or (metrics and (metrics["running"] or metrics["queued"])):
                state = ACTIVITY_QUERY
                busy_buckets += 1
            elif metrics and metrics["sessions"]:
                state = ACTIVITY_OTHER
        activity.append({"t": edge, "state": state})

    return {
        "window": window,
        "bucket_seconds": int(grid.bucket.total_seconds()),
        "start": grid.start,
        "end": grid.end,
        "peak_query_count": [
            {
                "t": edge,
                "running": rollup.get(i, {}).get("running", 0),
                "queued": rollup.get(i, {}).get("queued", 0),
            }
            for i, edge in enumerate(grid.edges)
        ],
        "completed_query_count": [
            {"t": edge, "per_minute": round(completed[i] / bucket_minutes, 3)}
            for i, edge in enumerate(grid.edges)
        ],
        "activity": activity,
        "failures": [
            {"t": grid.edges[i], "reason": reason, "count": count}
            for i in sorted(failures)
            for reason, count in sorted(failures[i].items())
        ],
        "utilization": [
            _utilization_point(edge, rollup.get(i)) for i, edge in enumerate(grid.edges)
        ],
        "summary": {
            "uptime_s": round(uptime_s),
            # Share of *connected* time with query activity — the idle-vs-busy split
            # that says whether the idle timeout is set too generously. None when the
            # agent was never up, where a ratio would be a division by zero dressed
            # up as "0% busy".
            "busy_ratio": round(busy_buckets / up_buckets, 3) if up_buckets else None,
            "completed": sum(completed),
            "failed": failed_total,
            "idle_timeout_minutes": (
                int(agent.idle_timeout_s // 60) if agent.idle_timeout_s else None
            ),
        },
    }
