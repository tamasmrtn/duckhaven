"""The agent monitoring series: exact query-derived activity plus sampled resources.

Every test pins ``now`` so the grid is deterministic.
"""

from datetime import UTC, datetime, timedelta

import pytest

from api.models.agent import Agent, AgentLifecycleEvent, AgentMetricsMinute
from api.models.query import Query
from api.services import agent_monitoring
from api.services.agent_monitoring import PRESETS, build_monitoring, choose_bucket

pytestmark = pytest.mark.usefixtures("db_session")

NOW = datetime(2026, 9, 30, 12, 3, 30, tzinfo=UTC)


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


@pytest.fixture(autouse=True)
def _no_live_samples(monkeypatch):
    async def none(db, agent_id):
        return []

    monkeypatch.setattr(agent_monitoring, "agent_recent_samples", none)


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
            covered_s=kw.get("covered_s"),
            oom_kills=kw.get("oom_kills"),
        )
    )
    await db.commit()


async def add_event(db, agent, event, at, reason=None):
    db.add(AgentLifecycleEvent(agent_id=agent.id, event=event, reason=reason, at=at))
    await db.commit()


async def add_query(
    db,
    ws,
    agent,
    *,
    started,
    running=None,
    finished=None,
    status="done",
    error=None,
    origin=None,
    parked=False,
):
    db.add(
        Query(
            workspace_id=ws.id,
            agent_id=None if parked else agent.id,
            requested_agent_id=agent.id if parked else None,
            sql="select 1",
            status=status,
            origin=origin,
            error=error,
            started_at=started,
            running_at=running,
            finished_at=finished,
        )
    )
    await db.commit()


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


def total(data, key) -> float:
    """Undo the per-bucket average: query-seconds in that state over the range."""
    return sum(b[key] * b["seconds"] for b in data["buckets"])


# ── Range and grid ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("window", "bucket_s"),
    [
        ("1h", 60),
        ("3h", 120),
        ("8h", 300),
        ("12h", 300),
        ("24h", 600),
        ("3d", 1800),
        ("7d", 7200),
    ],
)
async def test_presets_choose_a_bucket_that_keeps_the_chart_readable(
    db_session, agent, window, bucket_s
):
    data = await build_monitoring(db_session, agent, window=window, now=NOW)
    assert data["bucket_seconds"] == bucket_s
    assert data["preset"] == window
    assert 60 <= len(data["buckets"]) <= 151


@pytest.mark.parametrize(
    ("span", "bucket"),
    [
        (timedelta(minutes=30), timedelta(minutes=1)),
        (timedelta(hours=5), timedelta(minutes=2)),
        (timedelta(days=2), timedelta(minutes=30)),
        (timedelta(days=30), timedelta(hours=6)),
    ],
)
def test_custom_ranges_get_the_smallest_bucket_that_fits(span, bucket):
    assert choose_bucket(span) == bucket


async def test_the_range_ends_now_and_the_last_bucket_is_partial(db_session, agent):
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    last = data["buckets"][-1]
    assert last["t"] == datetime(2026, 9, 30, 12, 3, tzinfo=UTC)
    assert last["seconds"] == 30
    assert last["partial"] is True
    assert data["range_end"] == NOW
    # The grid covers exactly the requested hour: its first bucket starts at the
    # range start, interior edges stay aligned so the axis doesn't jitter.
    first = data["buckets"][0]
    assert first["t"] == ago(hours=1)
    assert (first["seconds"], first["partial"]) == (30, True)
    assert not any(b["partial"] for b in data["buckets"][1:-1])
    assert sum(b["seconds"] for b in data["buckets"]) == 3600


async def test_a_custom_range_is_clamped_to_now_and_retention(db_session, agent, monkeypatch):
    from api.config import settings

    monkeypatch.setattr(settings, "agent_metrics_retention_hours", 24.0)
    data = await build_monitoring(
        db_session, agent, start=ago(days=10), end=NOW + timedelta(hours=1), now=NOW
    )
    assert data["preset"] is None
    assert data["range_end"] == NOW
    assert data["range_start"] == ago(hours=24)


async def test_state_seconds_sum_to_every_bucket(db_session, agent, workspace):
    await add_event(db_session, agent, "provisioning", ago(minutes=40))
    await add_event(db_session, agent, "connected", ago(minutes=39, seconds=30))
    await add_query(
        db_session,
        workspace,
        agent,
        started=ago(minutes=30),
        running=ago(minutes=29),
        finished=ago(minutes=20),
    )
    await add_event(db_session, agent, "disconnected", ago(minutes=10))
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    for b in data["buckets"]:
        parts = b["busy_s"] + b["idle_s"] + b["starting_s"] + b["down_s"] + b["unknown_s"]
        assert parts == pytest.approx(b["seconds"], abs=0.01)


# ── Busy and up time ─────────────────────────────────────────────────────────


async def test_busy_is_identical_at_every_zoom_level(db_session, agent, workspace):
    """The old bucket-flag ratio read 0 % to 67 % for one agent depending on the
    range. Measured from exact intervals it cannot move."""
    await add_event(db_session, agent, "connected", ago(minutes=50))
    for start, stop in ((40, 35), (38, 36), (20, 19.5), (5, 4)):
        await add_query(
            db_session,
            workspace,
            agent,
            started=ago(minutes=start, seconds=5),
            running=ago(minutes=start),
            finished=ago(minutes=stop),
        )

    results = []
    for kwargs in (
        {"window": "1h"},
        {"window": "3h"},
        {"window": "8h"},
        {"window": "24h"},
        {"window": "7d"},
        {"start": ago(minutes=55), "end": NOW},
    ):
        data = await build_monitoring(db_session, agent, now=NOW, **kwargs)
        assert sum(b["busy_s"] for b in data["buckets"]) == pytest.approx(
            data["summary"]["busy_s"], abs=1
        )
        results.append((data["summary"]["busy_s"], data["summary"]["uptime_s"]))

    # 5 min (two overlapping queries count once) + 30 s + 1 min, over 50 min up.
    assert set(results) == {(390, 3000)}


async def test_uptime_never_counts_the_future(db_session, agent):
    """The old grid ran to the end of the current bucket, up to ten minutes ahead."""
    await add_event(db_session, agent, "connected", ago(minutes=10))
    for window in ("1h", "8h", "24h"):
        data = await build_monitoring(db_session, agent, window=window, now=NOW)
        assert data["summary"]["uptime_s"] == 600


async def test_the_query_that_wakes_an_elastic_agent_counts(db_session, agent, workspace):
    """It lands while the agent is still starting; it used to be dropped as 'down'."""
    await add_event(db_session, agent, "provisioning", ago(minutes=10))
    await add_event(db_session, agent, "connected", ago(minutes=9))
    await add_query(
        db_session,
        workspace,
        agent,
        started=ago(minutes=10),
        running=ago(minutes=8, seconds=58),
        finished=ago(minutes=8),
    )

    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    assert data["summary"]["busy_s"] == 58
    assert total(data, "compute_wait_avg") == pytest.approx(60, abs=0.5)
    assert total(data, "queued_avg") == pytest.approx(2, abs=0.5)
    assert total(data, "running_avg") == pytest.approx(58, abs=0.5)


async def test_an_idle_held_session_is_idle(db_session, agent):
    """A connection held open with nothing running is idle time, whatever the agent's
    admission count said (the old rollup counted it as a running query)."""
    await add_event(db_session, agent, "connected", ago(minutes=30))
    await add_minute(
        db_session, agent, ago(minutes=20).replace(second=0), running_max=1, session_max=1
    )
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    assert data["summary"]["busy_s"] == 0
    assert data["summary"]["busy_ratio"] == 0
    assert data["summary"]["idle_s"] == 1800


async def test_a_never_up_agent_has_no_busy_ratio(db_session, agent):
    await add_event(db_session, agent, "terminated", ago(days=2))
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    assert data["summary"]["uptime_s"] == 0
    assert data["summary"]["busy_ratio"] is None


async def test_an_agent_with_no_trail_reads_unknown_not_down(db_session, agent):
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    assert {s["state"] for s in data["spans"]} == {"unknown"}
    assert sum(b["down_s"] for b in data["buckets"]) == 0


async def test_a_query_is_evidence_the_agent_was_up_when_the_trail_is_silent(
    db_session, agent, workspace
):
    await add_query(
        db_session,
        workspace,
        agent,
        started=ago(minutes=30),
        running=ago(minutes=30),
        finished=ago(minutes=29),
    )
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    assert data["summary"]["busy_s"] == 60
    assert data["summary"]["uptime_s"] == 60


async def test_a_lapsed_agent_stops_being_up_at_its_last_ping(db_session, agent):
    """The trail says connected, but the agent is gone. The page ends the run at the
    last proof of life instead of drawing days of idle uptime."""
    await add_event(db_session, agent, "connected", ago(minutes=60))
    agent.last_ping_at = ago(minutes=30)
    await db_session.commit()

    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    assert data["summary"]["uptime_s"] == 1800
    assert data["spans"][-1]["state"] == "down"


async def test_a_stale_running_row_stops_counting_when_the_agent_went_away(
    db_session, agent, workspace
):
    await add_event(db_session, agent, "connected", ago(minutes=50))
    await add_event(db_session, agent, "disconnected", ago(minutes=30))
    await add_query(
        db_session,
        workspace,
        agent,
        started=ago(minutes=40),
        running=ago(minutes=40),
        status="running",
    )
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    assert data["summary"]["busy_s"] == 600
    assert data["summary"]["peak_running"] == 1


async def test_a_query_still_running_counts_until_now(db_session, agent, workspace):
    await add_event(db_session, agent, "connected", ago(minutes=50))
    await add_query(
        db_session,
        workspace,
        agent,
        started=ago(minutes=2),
        running=ago(minutes=2),
        status="running",
    )
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    assert data["summary"]["busy_s"] == 120
    assert data["buckets"][-1]["running_avg"] == 1.0


# ── Concurrency and waiting ──────────────────────────────────────────────────


async def test_average_concurrency_is_query_seconds_over_bucket_seconds(
    db_session, agent, workspace
):
    """Little's law. Two back-to-back half-minute queries average one, and peak at one."""
    await add_event(db_session, agent, "connected", ago(hours=1))
    minute = ago(minutes=10).replace(second=0)
    for offset in (0, 30):
        await add_query(
            db_session,
            workspace,
            agent,
            started=minute + timedelta(seconds=offset),
            running=minute + timedelta(seconds=offset),
            finished=minute + timedelta(seconds=offset + 30),
        )
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    bucket = next(b for b in data["buckets"] if b["t"] == minute)
    assert bucket["running_avg"] == 1.0
    assert bucket["peak_running"] == 1


async def test_sub_second_queries_are_all_seen(db_session, agent, workspace):
    """The sampler missed five of five 0.5 s queries; timestamps miss none."""
    await add_event(db_session, agent, "connected", ago(hours=1))
    minute = ago(minutes=10).replace(second=0)
    for k in range(5):
        at = minute + timedelta(seconds=8 * k)
        await add_query(
            db_session,
            workspace,
            agent,
            started=at,
            running=at,
            finished=at + timedelta(milliseconds=500),
        )
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    bucket = next(b for b in data["buckets"] if b["t"] == minute)
    assert bucket["peak_running"] == 1
    assert bucket["done"] == 5
    assert bucket["busy_s"] == pytest.approx(2.5)


async def test_overlapping_queries_peak_at_their_true_concurrency(db_session, agent, workspace):
    await add_event(db_session, agent, "connected", ago(hours=1))
    for _ in range(3):
        await add_query(
            db_session,
            workspace,
            agent,
            started=ago(minutes=10),
            running=ago(minutes=10),
            finished=ago(minutes=9),
        )
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    assert data["summary"]["peak_running"] == 3


async def test_wait_p95_is_bucketed_where_the_wait_ended(db_session, agent, workspace):
    await add_event(db_session, agent, "connected", ago(hours=1))
    minute = ago(minutes=10).replace(second=0)
    for wait_s in (1, 2, 30):
        await add_query(
            db_session,
            workspace,
            agent,
            started=minute - timedelta(seconds=wait_s),
            running=minute,
            finished=minute + timedelta(seconds=5),
        )
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    bucket = next(b for b in data["buckets"] if b["t"] == minute)
    assert bucket["wait_p95_ms"] == 30000
    assert bucket["wait_n"] == 3
    assert data["summary"]["wait_p95_ms"] == 30000


async def test_a_parked_run_that_never_got_compute_shows_as_waiting_for_it(
    db_session, agent, workspace
):
    await add_event(db_session, agent, "terminated", ago(hours=2))
    await add_query(
        db_session,
        workspace,
        agent,
        started=ago(minutes=20),
        finished=ago(minutes=10),
        status="failed",
        error="No compute became available for this run.",
        parked=True,
    )
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    assert total(data, "compute_wait_avg") == pytest.approx(600, abs=0.5)
    assert data["summary"]["failed_by_reason"] == {"no_compute": 1}


# ── Outcomes ─────────────────────────────────────────────────────────────────


async def test_outcomes_are_counted_where_the_query_finished(db_session, agent, workspace):
    await add_event(db_session, agent, "connected", ago(hours=1))
    at = ago(minutes=10)
    for status, error in (
        ("done", None),
        ("done", None),
        ("cancelled", None),
        ("failed", "queue full"),
        ("failed", "Out of Memory Error: could not allocate"),
    ):
        await add_query(
            db_session,
            workspace,
            agent,
            started=at,
            running=at,
            finished=at,
            status=status,
            error=error,
        )
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    bucket = next(b for b in data["buckets"] if b["done"])
    assert bucket["done"] == 2
    assert bucket["cancelled"] == 1
    assert bucket["failed"] == {"out_of_memory": 1, "queue_full": 1}
    summary = data["summary"]
    assert (summary["finished"], summary["failed"], summary["cancelled"]) == (5, 2, 1)


async def test_internal_queries_are_excluded_but_session_statements_count(
    db_session, agent, workspace
):
    await add_event(db_session, agent, "connected", ago(hours=1))
    for origin in ("metadata", "maintenance", "sample", "session", None):
        await add_query(
            db_session,
            workspace,
            agent,
            started=ago(minutes=10),
            running=ago(minutes=10),
            finished=ago(minutes=9),
            origin=origin,
        )
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    assert data["summary"]["finished"] == 2
    assert data["summary"]["peak_running"] == 2


async def test_another_agents_activity_never_leaks_in(db_session, agent, workspace):
    other = Agent(name="other", status="healthy")
    db_session.add(other)
    await db_session.commit()
    await add_query(
        db_session,
        workspace,
        other,
        started=ago(minutes=10),
        running=ago(minutes=10),
        finished=ago(minutes=9),
    )
    await add_minute(db_session, other, ago(minutes=10).replace(second=0), cpu_avg=90.0)
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    assert data["summary"]["finished"] == 0
    assert all(b["cpu_avg"] is None for b in data["buckets"])


# ── Resources ────────────────────────────────────────────────────────────────


async def test_unmeasured_is_null_and_a_measured_zero_is_zero(db_session, agent):
    minute = ago(minutes=10).replace(second=0)
    await add_minute(db_session, agent, minute, cpu_avg=0.0, mem_avg=0.0)
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    measured = next(b for b in data["buckets"] if b["t"] == minute)
    unmeasured = next(b for b in data["buckets"] if b["t"] == minute - timedelta(minutes=1))
    assert measured["cpu_avg"] == 0.0
    assert unmeasured["cpu_avg"] is None
    assert data["summary"]["cpu_peak"] == 0.0


async def test_resource_averages_are_weighted_and_peaks_kept(db_session, agent):
    """At 10-minute buckets a short spike still shows as the bucket's peak."""
    start = ago(hours=3).replace(minute=0, second=0)
    await add_minute(db_session, agent, start, cpu_avg=10.0, cpu_max=12.0, sample_count=30)
    await add_minute(
        db_session,
        agent,
        start + timedelta(minutes=1),
        cpu_avg=40.0,
        cpu_max=99.0,
        mem_max=64.0,
        sample_count=10,
    )
    data = await build_monitoring(db_session, agent, window="24h", now=NOW)
    bucket = next(b for b in data["buckets"] if b["cpu_avg"] is not None)
    assert bucket["cpu_avg"] == pytest.approx((10 * 30 + 40 * 10) / 40)
    assert bucket["cpu_max"] == 99.0
    assert bucket["mem_max"] == 64.0


async def test_coverage_and_oom_kills_are_reported_when_measured(db_session, agent):
    minute = ago(minutes=10).replace(second=0)
    await add_minute(db_session, agent, minute, covered_s=30.0, oom_kills=1)
    await add_minute(db_session, agent, minute - timedelta(minutes=1))  # an older agent
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    by_t = {b["t"]: b for b in data["buckets"]}
    assert by_t[minute]["coverage"] == 0.5
    assert by_t[minute]["oom_kills"] == 1
    assert by_t[minute - timedelta(minutes=1)]["coverage"] is None
    assert by_t[minute - timedelta(minutes=1)]["oom_kills"] is None


async def test_live_samples_fill_the_minute_not_yet_written(db_session, agent, monkeypatch):
    """Without them the right-hand edge of the charts was a minute stale."""
    persisted = datetime(2026, 9, 30, 12, 2, tzinfo=UTC)
    await add_minute(db_session, agent, persisted, cpu_avg=10.0, cpu_max=10.0)

    async def samples(db, agent_id):
        return [
            # Already in the rollup: must not be counted twice.
            {"sampled_at": "2026-09-30T12:02:50+00:00", "cpu_percent": 99.0, "memory_percent": 1.0},
            {
                "sampled_at": "2026-09-30T12:03:10+00:00",
                "cpu_percent": 50.0,
                "memory_percent": 20.0,
                "memory_peak_percent": 35.0,
                "interval_s": 2.0,
            },
            {
                "sampled_at": "2026-09-30T12:03:12+00:00",
                "cpu_percent": 70.0,
                "memory_percent": 22.0,
                "interval_s": 2.0,
            },
        ]

    monkeypatch.setattr(agent_monitoring, "agent_recent_samples", samples)
    data = await build_monitoring(db_session, agent, window="1h", now=NOW)
    by_t = {b["t"]: b for b in data["buckets"]}
    assert by_t[persisted]["cpu_max"] == 10.0
    current = by_t[datetime(2026, 9, 30, 12, 3, tzinfo=UTC)]
    assert current["cpu_avg"] == 60.0
    assert current["mem_max"] == 35.0
    assert data["summary"]["resources_as_of"] == datetime(2026, 9, 30, 12, 3, 12, tzinfo=UTC)


async def test_a_past_range_does_not_ask_for_live_samples(db_session, agent, monkeypatch):
    async def boom(db, agent_id):
        raise AssertionError("a range ending in the past has no live minute")

    monkeypatch.setattr(agent_monitoring, "agent_recent_samples", boom)
    await build_monitoring(db_session, agent, start=ago(hours=5), end=ago(hours=4), now=NOW)


def test_every_preset_is_documented_in_order():
    assert list(PRESETS) == ["1h", "3h", "8h", "12h", "24h", "3d", "7d"]
