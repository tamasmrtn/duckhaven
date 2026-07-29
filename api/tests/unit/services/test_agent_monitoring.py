from datetime import UTC, datetime, timedelta

import pytest

from api.models.agent import Agent, AgentLifecycleEvent, AgentMetricsMinute
from api.models.query import Query
from api.services.agent_monitoring import (
    ACTIVITY_DOWN,
    ACTIVITY_OTHER,
    ACTIVITY_QUERY,
    ACTIVITY_READY,
    ACTIVITY_STARTING,
    ACTIVITY_UNKNOWN,
    WINDOWS,
    build_grid,
    build_monitoring,
)

pytestmark = pytest.mark.usefixtures("db_session")


@pytest.fixture
async def agent(db_session):
    a = Agent(name="mon-agent", status="healthy", provider="null", lifecycle="running")
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    return a


@pytest.fixture
async def workspace(db_session):
    from api.models.user import User
    from api.services.auth import hash_password
    from tests.unit.conftest import seed_workspace

    user = User(email="mon@x.local", password_hash=hash_password("pw"), name="M", role="admin")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    ws, _ = await seed_workspace(db_session, user_id=user.id, slug="mon", name="Mon")
    return ws


async def add_minute(db, agent, minute, **kw):
    db.add(
        AgentMetricsMinute(
            agent_id=agent.id,
            minute=minute,
            cpu_avg=kw.get("cpu_avg", 0.0),
            cpu_max=kw.get("cpu_max", 0.0),
            mem_avg=kw.get("mem_avg", 0.0),
            mem_max=kw.get("mem_max", 0.0),
            running_max=kw.get("running_max", 0),
            queued_max=kw.get("queued_max", 0),
            session_max=kw.get("session_max", 0),
            sample_count=kw.get("sample_count", 30),
        )
    )
    await db.commit()


async def add_event(db, agent, event, at, reason=None):
    db.add(AgentLifecycleEvent(agent_id=agent.id, event=event, reason=reason, at=at))
    await db.commit()


async def add_query(db, ws, agent, finished_at, status="done", error=None, origin=None):
    db.add(
        Query(
            workspace_id=ws.id,
            agent_id=agent.id,
            sql="select 1",
            status=status,
            origin=origin,
            error=error,
            started_at=finished_at - timedelta(seconds=1),
            finished_at=finished_at,
        )
    )
    await db.commit()


# ── Grid ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("window", "expected_buckets", "expected_bucket_s"),
    [("1h", 60, 60), ("3h", 90, 120), ("8h", 96, 300), ("12h", 144, 300), ("24h", 144, 600)],
)
def test_every_window_lands_between_60_and_144_points(window, expected_buckets, expected_bucket_s):
    grid = build_grid(window)
    assert grid.count == expected_buckets
    assert grid.bucket.total_seconds() == expected_bucket_s
    assert 60 <= grid.count <= 144


def test_grid_is_aligned_to_the_bucket_not_to_now():
    """Otherwise every poll shifts the axis and the bars visibly jitter."""
    now = datetime(2026, 7, 28, 10, 3, 47, 123456, tzinfo=UTC)
    grid = build_grid("8h", now=now)
    assert grid.end.second == 0 and grid.end.microsecond == 0
    assert int(grid.end.timestamp()) % 300 == 0
    assert grid.end - grid.start == WINDOWS["8h"][0]


def test_grid_index_of_rejects_timestamps_outside_the_window():
    grid = build_grid("1h", now=datetime(2026, 7, 28, 10, 0, tzinfo=UTC))
    assert grid.index_of(grid.start) == 0
    assert grid.index_of(grid.start - timedelta(seconds=1)) is None
    assert grid.index_of(grid.end) is None


# ── Peak query count (from the rollup) ───────────────────────────────────────


async def test_peak_query_count_takes_the_max_across_the_bucket(db_session, agent):
    grid = build_grid("8h")
    # Two minutes inside one 5-minute bucket, with different depths.
    await add_minute(
        db_session, agent, grid.start + timedelta(minutes=1), running_max=1, queued_max=0
    )
    await add_minute(
        db_session, agent, grid.start + timedelta(minutes=2), running_max=3, queued_max=4
    )

    data = await build_monitoring(db_session, agent, "8h")
    assert data["peak_query_count"][0] == {"t": grid.edges[0], "running": 3, "queued": 4}


async def test_buckets_with_no_rollup_report_zero_depth(db_session, agent):
    data = await build_monitoring(db_session, agent, "1h")
    assert all(p["running"] == 0 and p["queued"] == 0 for p in data["peak_query_count"])


# ── Completed query count ────────────────────────────────────────────────────


async def test_completed_count_is_normalized_per_minute(db_session, agent, workspace):
    grid = build_grid("8h")
    at = grid.start + timedelta(minutes=1)
    for _ in range(10):
        await add_query(db_session, workspace, agent, at)

    data = await build_monitoring(db_session, agent, "8h")
    # 10 queries in one 5-minute bucket = 2/min, matching the chart's stated unit.
    assert data["completed_query_count"][0]["per_minute"] == 2.0
    assert data["summary"]["completed"] == 10


async def test_completed_count_includes_failed_and_cancelled(db_session, agent, workspace):
    """A completed query is one that stopped running, however it ended.

    Counting only successes would make a failing agent look idle rather than busy.
    """
    grid = build_grid("1h")
    at = grid.start + timedelta(seconds=10)
    await add_query(db_session, workspace, agent, at, status="done")
    await add_query(db_session, workspace, agent, at, status="failed", error="boom")
    await add_query(db_session, workspace, agent, at, status="cancelled")

    data = await build_monitoring(db_session, agent, "1h")
    assert data["summary"]["completed"] == 3
    assert data["summary"]["failed"] == 2


async def test_internal_origins_are_excluded(db_session, agent, workspace):
    """Must match the history list, or the chart and the table disagree."""
    grid = build_grid("1h")
    at = grid.start + timedelta(seconds=10)
    await add_query(db_session, workspace, agent, at, origin="sample")
    await add_query(db_session, workspace, agent, at, origin="metadata")
    await add_query(db_session, workspace, agent, at, origin="scheduled")

    data = await build_monitoring(db_session, agent, "1h")
    assert data["summary"]["completed"] == 1


async def test_queries_outside_the_window_are_ignored(db_session, agent, workspace):
    grid = build_grid("1h")
    await add_query(db_session, workspace, agent, grid.start - timedelta(minutes=5))

    data = await build_monitoring(db_session, agent, "1h")
    assert data["summary"]["completed"] == 0


# ── Failures ─────────────────────────────────────────────────────────────────


async def test_failures_are_grouped_by_classified_reason(db_session, agent, workspace):
    grid = build_grid("1h")
    at = grid.start + timedelta(seconds=10)
    await add_query(db_session, workspace, agent, at, status="failed", error="queue full")
    await add_query(db_session, workspace, agent, at, status="failed", error="queue full")
    await add_query(db_session, workspace, agent, at, status="failed", error="Out of Memory Error")
    await add_query(db_session, workspace, agent, at, status="cancelled")

    data = await build_monitoring(db_session, agent, "1h")
    by_reason = {f["reason"]: f["count"] for f in data["failures"]}
    assert by_reason == {"queue_full": 2, "out_of_memory": 1, "cancelled": 1}


# ── Activity timeline ────────────────────────────────────────────────────────


async def test_an_agent_with_no_trail_reads_unknown_not_down(db_session, agent):
    """An agent older than the trail has no history; that is not an outage."""
    data = await build_monitoring(db_session, agent, "1h")
    assert {p["state"] for p in data["activity"]} == {ACTIVITY_UNKNOWN}
    assert data["summary"]["uptime_s"] == 0
    assert data["summary"]["busy_ratio"] is None


async def test_reported_telemetry_counts_as_evidence_the_agent_was_up(db_session, agent):
    """The upgrade path: an agent older than the lifecycle trail.

    It has no events at all, but it has been reporting samples all along. Drawing
    that as "no data" would hide a whole history we demonstrably hold.
    """
    grid = build_grid("1h")
    await add_minute(db_session, agent, grid.edges[3], running_max=2)
    await add_minute(db_session, agent, grid.edges[4], running_max=0)

    data = await build_monitoring(db_session, agent, "1h")
    states = [p["state"] for p in data["activity"]]
    assert states[3] == ACTIVITY_QUERY
    assert states[4] == ACTIVITY_READY
    # Buckets with no evidence either way stay honestly unknown.
    assert states[0] == ACTIVITY_UNKNOWN
    assert data["summary"]["uptime_s"] == 120
    assert data["summary"]["busy_ratio"] == pytest.approx(0.5)


async def test_a_finished_query_alone_is_evidence_the_agent_was_up(db_session, agent, workspace):
    grid = build_grid("1h")
    await add_query(db_session, workspace, agent, grid.edges[7] + timedelta(seconds=5))

    states = [p["state"] for p in (await build_monitoring(db_session, agent, "1h"))["activity"]]
    assert states[7] == ACTIVITY_QUERY


async def test_state_is_seeded_from_the_last_event_before_the_window(db_session, agent):
    """An agent quietly up for a week has no events inside an 8h window."""
    grid = build_grid("8h")
    await add_event(db_session, agent, "connected", grid.start - timedelta(days=2))

    data = await build_monitoring(db_session, agent, "8h")
    assert {p["state"] for p in data["activity"]} == {ACTIVITY_READY}
    assert data["summary"]["uptime_s"] == pytest.approx(8 * 3600, abs=1)


async def test_disconnect_inside_the_window_splits_the_timeline(db_session, agent):
    grid = build_grid("8h")
    await add_event(db_session, agent, "connected", grid.start - timedelta(hours=1))
    await add_event(db_session, agent, "disconnected", grid.start + timedelta(hours=4))

    data = await build_monitoring(db_session, agent, "8h")
    states = [p["state"] for p in data["activity"]]
    assert states[0] == ACTIVITY_READY
    assert states[-1] == ACTIVITY_DOWN
    # Half the window up; uptime counts real seconds, not buckets.
    assert data["summary"]["uptime_s"] == pytest.approx(4 * 3600, abs=60)


async def test_provisioning_shows_as_a_distinct_starting_band(db_session, agent):
    grid = build_grid("1h")
    await add_event(db_session, agent, "provisioning", grid.start + timedelta(minutes=10))
    await add_event(db_session, agent, "connected", grid.start + timedelta(minutes=13))

    states = [p["state"] for p in (await build_monitoring(db_session, agent, "1h"))["activity"]]
    assert states[0] == ACTIVITY_UNKNOWN
    assert states[10:13] == [ACTIVITY_STARTING] * 3
    assert states[13] == ACTIVITY_READY


async def test_a_bucket_takes_the_state_that_covered_most_of_it(db_session, agent):
    """An agent up 2s into a 10-minute bucket was up for that bucket."""
    grid = build_grid("24h")
    await add_event(db_session, agent, "connected", grid.edges[5] + timedelta(seconds=2))

    states = [p["state"] for p in (await build_monitoring(db_session, agent, "24h"))["activity"]]
    assert states[5] == ACTIVITY_READY


async def test_a_restart_reusing_the_row_still_shows_both_runs(db_session, agent):
    """The agents row is mutated in place; only the trail remembers the first run."""
    grid = build_grid("8h")
    await add_event(db_session, agent, "connected", grid.start - timedelta(hours=1))
    await add_event(
        db_session, agent, "terminating", grid.start + timedelta(hours=2), reason="idle"
    )
    await add_event(
        db_session, agent, "provisioning", grid.start + timedelta(hours=5), reason="restart"
    )
    await add_event(db_session, agent, "connected", grid.start + timedelta(hours=6))

    states = [p["state"] for p in (await build_monitoring(db_session, agent, "8h"))["activity"]]
    assert ACTIVITY_READY in states
    assert ACTIVITY_DOWN in states
    assert ACTIVITY_STARTING in states


async def test_connected_buckets_refine_into_query_other_and_ready(db_session, agent, workspace):
    grid = build_grid("1h")
    await add_event(db_session, agent, "connected", grid.start - timedelta(minutes=5))
    # Bucket 2: queries ran. Bucket 4: only a held SQL session. Bucket 6: nothing.
    await add_minute(db_session, agent, grid.edges[2], running_max=2)
    await add_minute(db_session, agent, grid.edges[4], session_max=1)

    data = await build_monitoring(db_session, agent, "1h")
    states = [p["state"] for p in data["activity"]]
    assert states[2] == ACTIVITY_QUERY
    assert states[4] == ACTIVITY_OTHER
    assert states[6] == ACTIVITY_READY


async def test_busy_ratio_is_the_share_of_connected_time_with_query_activity(db_session, agent):
    grid = build_grid("1h")
    await add_event(db_session, agent, "connected", grid.start - timedelta(minutes=5))
    for i in range(15):  # 15 of 60 one-minute buckets busy
        await add_minute(db_session, agent, grid.edges[i], running_max=1)

    data = await build_monitoring(db_session, agent, "1h")
    assert data["summary"]["busy_ratio"] == pytest.approx(0.25)


async def test_a_down_agent_reports_no_busy_ratio_rather_than_zero_percent(db_session, agent):
    grid = build_grid("1h")
    await add_event(db_session, agent, "disconnected", grid.start - timedelta(minutes=5))

    data = await build_monitoring(db_session, agent, "1h")
    assert data["summary"]["uptime_s"] == 0
    assert data["summary"]["busy_ratio"] is None


# ── Utilization ──────────────────────────────────────────────────────────────


async def test_utilization_averages_are_weighted_by_sample_count(db_session, agent):
    grid = build_grid("8h")
    # Two minutes in one bucket, one of them a short partial minute.
    await add_minute(db_session, agent, grid.edges[0], cpu_avg=10, cpu_max=15, sample_count=30)
    await add_minute(
        db_session,
        agent,
        grid.edges[0] + timedelta(minutes=1),
        cpu_avg=40,
        cpu_max=80,
        sample_count=10,
    )

    point = (await build_monitoring(db_session, agent, "8h"))["utilization"][0]
    assert point["cpu_avg"] == pytest.approx((10 * 30 + 40 * 10) / 40)
    assert point["cpu_max"] == 80.0


async def test_unmeasured_buckets_are_null_not_zero(db_session, agent):
    """A gap must read as "not measured", not as a genuine 0% reading."""
    point = (await build_monitoring(db_session, agent, "1h"))["utilization"][0]
    assert point["cpu_avg"] is None and point["mem_max"] is None


# ── Isolation ────────────────────────────────────────────────────────────────


async def test_another_agents_telemetry_never_leaks_in(db_session, agent, workspace):
    other = Agent(name="other", status="healthy")
    db_session.add(other)
    await db_session.commit()
    await db_session.refresh(other)

    grid = build_grid("1h")
    await add_minute(db_session, other, grid.edges[0], running_max=9)
    await add_query(db_session, workspace, other, grid.start + timedelta(seconds=5))
    await add_event(db_session, other, "connected", grid.start - timedelta(hours=1))

    data = await build_monitoring(db_session, agent, "1h")
    assert data["summary"]["completed"] == 0
    assert data["peak_query_count"][0]["running"] == 0
    assert data["activity"][0]["state"] == ACTIVITY_UNKNOWN
