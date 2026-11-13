"""Tests for the Prometheus /metrics endpoint and instrumentation.

The prometheus_client default REGISTRY is process-global, so counter/histogram
assertions compare before/after deltas rather than absolute values. Scrape-time
gauges are cleared and repopulated on every scrape, so they are safe to assert
absolutely after a scrape.
"""

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from httpx import AsyncClient
from prometheus_client import REGISTRY
from sqlalchemy.pool import QueuePool

from api.config import settings
from api.db.session import engine
from api.metrics import record_query_submitted, set_scan_leader
from api.models.agent import Agent
from api.models.maintenance import MaintenancePolicy, MaintenanceRecommendation
from api.models.query import Query
from api.services import query as query_service
from api.services.agent_registry import registry
from api.services.polaris import PolarisClient
from duckhaven_shared.protocol import Frame, FrameType

RID = settings.replica_id


@pytest.fixture(autouse=True)
def _clean_metrics_state():
    """Isolate global registry / leader state across tests."""
    registry._connections.clear()
    set_scan_leader(False)
    yield
    registry._connections.clear()
    set_scan_leader(False)


def _value(name: str, labels: dict[str, str] | None = None) -> float | None:
    return REGISTRY.get_sample_value(name, labels or {})


def _sample(**overrides) -> dict:
    base = {
        "cpu_percent": 33.0,
        "memory_percent": 50.0,
        "running_queries": 2,
        "queued_queries": 3,
        "active_profile": "decaying_3",
        "sampled_at": "2026-06-05T00:00:00Z",
    }
    base.update(overrides)
    return base


async def _make_query(db, *, origin: str | None) -> Query:
    agent = Agent(name="runner", status="healthy")
    db.add(agent)
    await db.flush()
    q = Query(
        workspace_id=uuid.uuid4(),
        agent_id=agent.id,
        sql="SELECT 1",
        status="running",
        origin=origin,
        started_at=datetime.now(UTC),
    )
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return q


async def _apply(db, frame: Frame) -> None:
    """Apply ``frame`` as the agent its query was dispatched to."""
    row = await db.get(Query, uuid.UUID(frame.payload["query_id"]))
    assert row is not None and row.agent_id is not None, "dispatch the row to an agent first"
    await query_service.handle_agent_frame(db, frame, agent_id=row.agent_id)


async def _done_frame(query_id, *, status="done", duration_ms=1200, result_bytes=4096) -> Frame:
    return Frame(
        type=FrameType.QUERY_DONE,
        payload={
            "query_id": str(query_id),
            "status": status,
            "duration_ms": duration_ms,
            "result_bytes": result_bytes,
        },
    )


async def test_metrics_exposition_format(client: AsyncClient):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    assert "# HELP" in body and "# TYPE" in body
    assert "duckhaven_queries_total" in body


async def test_metrics_disabled_returns_404(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(settings, "metrics_enabled", False)
    resp = await client.get("/metrics")
    assert resp.status_code == 404


async def test_query_submitted_counter_increments():
    before = _value("duckhaven_queries_submitted_total", {"replica_id": RID}) or 0
    record_query_submitted()
    after = _value("duckhaven_queries_submitted_total", {"replica_id": RID})
    assert after == before + 1


async def test_terminal_counter_and_histograms(db_session):
    before = _value("duckhaven_queries_total", {"replica_id": RID, "status": "done"}) or 0
    dur_before = _value("duckhaven_query_duration_seconds_count", {"replica_id": RID}) or 0
    bytes_before = _value("duckhaven_query_result_bytes_count", {"replica_id": RID}) or 0

    query = await _make_query(db_session, origin=None)
    await _apply(db_session, await _done_frame(query.id))

    assert _value("duckhaven_queries_total", {"replica_id": RID, "status": "done"}) == before + 1
    assert _value("duckhaven_query_duration_seconds_count", {"replica_id": RID}) == dur_before + 1
    assert _value("duckhaven_query_result_bytes_count", {"replica_id": RID}) == bytes_before + 1


async def test_failed_query_counts_but_skips_histograms(db_session):
    before = _value("duckhaven_queries_total", {"replica_id": RID, "status": "failed"}) or 0
    dur_before = _value("duckhaven_query_duration_seconds_count", {"replica_id": RID}) or 0

    query = await _make_query(db_session, origin=None)
    await _apply(db_session, await _done_frame(query.id, status="failed"))

    assert _value("duckhaven_queries_total", {"replica_id": RID, "status": "failed"}) == before + 1
    # Duration/result histograms only observe successful queries. Both sides are
    # coerced the same way, for the reason spelled out in test_internal_queries_
    # excluded below: an untouched histogram reads back as None, so coercing only
    # `before` compared None to 0 and failed on whichever tests xdist happened to
    # schedule alongside this one.
    assert (
        _value("duckhaven_query_duration_seconds_count", {"replica_id": RID}) or 0
    ) == dur_before


async def test_failures_are_counted_by_cause(db_session):
    """A failure ratio for alerting can leave the users' own SQL mistakes out."""
    q = await _make_query(db_session, origin=None)
    before = _value("duckhaven_query_failures_total", {"replica_id": RID, "reason": "sql_error"})
    frame = await _done_frame(q.id, status="failed")
    frame.payload["error"] = "Catalog Error: Table with name nope does not exist!"
    await _apply(db_session, frame)
    after = _value("duckhaven_query_failures_total", {"replica_id": RID, "reason": "sql_error"})
    assert (after or 0) - (before or 0) == 1


async def test_internal_queries_excluded(db_session):
    # Both sides are coerced the same way. A counter no test in this worker has
    # touched yet reads back as None rather than 0, so coercing only `before`
    # made this compare None to 0 and fail purely on which tests xdist happened
    # to put alongside it.
    label = {"replica_id": RID, "status": "done"}
    before = _value("duckhaven_queries_total", label) or 0
    query = await _make_query(db_session, origin="maintenance")
    await _apply(db_session, await _done_frame(query.id))
    assert (_value("duckhaven_queries_total", label) or 0) == before


async def test_a_dashboard_run_counts_as_interactive(db_session):
    """A dashboard's widgets are run for someone waiting on them, like their own run."""
    label = {"replica_id": RID, "status": "done"}
    before = _value("duckhaven_queries_total", label) or 0
    query = await _make_query(db_session, origin="dashboard")
    await _apply(db_session, await _done_frame(query.id))
    assert (_value("duckhaven_queries_total", label) or 0) == before + 1


async def test_agent_gauges_from_local_registry(client: AsyncClient, db_session):
    agent = Agent(name="busy-agent", status="healthy")
    db_session.add(agent)
    await db_session.commit()
    registry.register(agent.id, object())  # type: ignore[arg-type]
    registry.record_metrics(agent.id, _sample())

    await client.get("/metrics")

    labels = {"replica_id": RID, "agent_id": str(agent.id), "agent_name": "busy-agent"}
    assert _value("duckhaven_agent_up", labels) == 1
    assert _value("duckhaven_agent_cpu_percent", labels) == 33.0
    assert _value("duckhaven_agent_running_queries", labels) == 2
    assert _value("duckhaven_agent_queued_queries", labels) == 3
    assert _value("duckhaven_agent_active_profile_info", {**labels, "profile": "decaying_3"}) == 1


async def test_new_agent_measurements_are_exported(client: AsyncClient, db_session):
    """Executing vs idle sessions, the interval memory peak, and cumulative CPU and
    OOM counters — counters so no scrape interval can miss an event."""
    agent = Agent(name="measured-agent", status="healthy")
    db_session.add(agent)
    await db_session.commit()
    registry.register(agent.id, object())  # type: ignore[arg-type]
    registry.record_metrics(
        agent.id,
        _sample(
            executing_queries=1,
            idle_sessions=2,
            memory_peak_percent=71.5,
            cpu_seconds_total=1234.5,
            oom_kills_total=3,
        ),
    )

    await client.get("/metrics")

    labels = {"replica_id": RID, "agent_id": str(agent.id), "agent_name": "measured-agent"}
    assert _value("duckhaven_agent_executing_queries", labels) == 1
    assert _value("duckhaven_agent_idle_sessions", labels) == 2
    assert _value("duckhaven_agent_memory_peak_percent", labels) == 71.5
    assert _value("duckhaven_agent_cpu_seconds_total", labels) == 1234.5
    assert _value("duckhaven_agent_oom_kills_total", labels) == 3


async def test_an_older_agent_has_no_series_for_what_it_cannot_measure(
    client: AsyncClient, db_session
):
    """Absent, not zero: a zero OOM counter would claim a measurement never taken."""
    agent = Agent(name="old-agent", status="healthy")
    db_session.add(agent)
    await db_session.commit()
    registry.register(agent.id, object())  # type: ignore[arg-type]
    registry.record_metrics(agent.id, _sample())

    await client.get("/metrics")

    labels = {"replica_id": RID, "agent_id": str(agent.id), "agent_name": "old-agent"}
    assert _value("duckhaven_agent_up", labels) == 1
    assert _value("duckhaven_agent_executing_queries", labels) is None
    assert _value("duckhaven_agent_oom_kills_total", labels) is None


async def test_peer_owned_agents_not_reported(client: AsyncClient, db_session):
    """HA: a replica reports only agents it owns (in its local ring buffer), so a
    peer-owned agent never appears here and `sum()` across replicas cannot double."""
    peer_agent = Agent(
        name="peer-agent",
        status="healthy",
        owner_url="http://other-replica:8000",
        last_ping_at=datetime.now(UTC),
    )
    db_session.add(peer_agent)
    await db_session.commit()
    # Deliberately NOT registered in the local registry.

    await client.get("/metrics")

    labels = {"replica_id": RID, "agent_id": str(peer_agent.id), "agent_name": "peer-agent"}
    assert _value("duckhaven_agent_up", labels) is None


async def test_db_pool_gauges(client: AsyncClient):
    await client.get("/metrics")
    val = _value("duckhaven_db_pool_size", {"replica_id": RID, "pool": "main"})
    if isinstance(engine.sync_engine.pool, QueuePool):
        assert val == settings.db_pool_size
    else:  # NullPool / SQLite-style pools don't expose sizing stats
        assert val is None


async def test_the_ducklake_pool_is_reported_separately_once_it_exists(client: AsyncClient):
    """Labelled alongside the main pool, and absent until the lazy engine is built."""
    from api.services.catalog_backends import ducklake

    # The engine is module-level; dispose it so the result doesn't depend on test order.
    await ducklake.dispose_engine()

    await client.get("/metrics")
    assert _value("duckhaven_db_pool_size", {"replica_id": RID, "pool": "ducklake"}) is None

    settings.ducklake_database_url = "postgresql+asyncpg://u:p@localhost:5432/ducklake"
    try:
        ducklake.get_engine()
        await client.get("/metrics")
        assert (
            _value("duckhaven_db_pool_size", {"replica_id": RID, "pool": "ducklake"})
            == settings.db_pool_size
        )
    finally:
        await ducklake.dispose_engine()


async def test_maintenance_gauges_leader_gated(client: AsyncClient, db_session):
    db_session.add(MaintenancePolicy(thresholds={}, last_scan_at=datetime(2026, 6, 1, tzinfo=UTC)))
    db_session.add(
        MaintenanceRecommendation(
            workspace_id=uuid.uuid4(),
            catalog_id=uuid.uuid4(),
            schema_name="public",
            table_name="events",
            kind="compact_small_files",
            severity="warning",
            confidence="high",
            rationale="many small files",
            status="open",
        )
    )
    await db_session.commit()

    # Non-leader: scanner gauges are absent.
    set_scan_leader(False)
    await client.get("/metrics")
    assert _value("duckhaven_maintenance_open_recommendations", {"severity": "warning"}) is None
    assert _value("duckhaven_maintenance_last_scan_timestamp_seconds") is None

    # Leader: scanner gauges are emitted.
    set_scan_leader(True)
    await client.get("/metrics")
    assert _value("duckhaven_maintenance_open_recommendations", {"severity": "warning"}) == 1
    assert _value("duckhaven_maintenance_last_scan_timestamp_seconds") is not None


async def test_http_metrics_middleware(client: AsyncClient):
    await client.get("/healthz")
    await client.get("/metrics")  # the scrape itself must not be counted

    counted = _value(
        "duckhaven_http_requests_total",
        {"replica_id": RID, "method": "GET", "route": "/healthz", "status": "200"},
    )
    assert counted is not None and counted >= 1
    not_counted = _value(
        "duckhaven_http_requests_total",
        {"replica_id": RID, "method": "GET", "route": "/metrics", "status": "200"},
    )
    assert not_counted is None


# ── Queue admission signals ───────────────────────────────────────────────────


async def test_queue_wait_recorded_on_first_running_transition(db_session):
    before = _value("duckhaven_query_queue_wait_seconds_count", {"replica_id": RID}) or 0
    query = await _make_query(db_session, origin=None)
    # Force the query back to "queued" so the PROGRESS frame is the first transition.
    query.status = "queued"
    await db_session.commit()
    await _apply(
        db_session,
        Frame(type=FrameType.QUERY_PROGRESS, payload={"query_id": str(query.id), "stage": "scan"}),
    )
    assert _value("duckhaven_query_queue_wait_seconds_count", {"replica_id": RID}) == before + 1


async def test_queue_wait_is_recorded_once_per_query(db_session):
    """Only the first progress frame is the queued -> running transition."""
    query = await _make_query(db_session, origin=None)
    query.status = "queued"
    await db_session.commit()
    frame = Frame(type=FrameType.QUERY_PROGRESS, payload={"query_id": str(query.id)})
    await _apply(db_session, frame)
    before = _value("duckhaven_query_queue_wait_seconds_count", {"replica_id": RID}) or 0
    await _apply(db_session, frame)
    assert _value("duckhaven_query_queue_wait_seconds_count", {"replica_id": RID}) == before


async def test_queue_wait_is_not_recorded_for_a_session_statement(db_session):
    """Interactive queries only, as before: a session statement waits on its lock."""
    before = _value("duckhaven_query_queue_wait_seconds_count", {"replica_id": RID}) or 0
    query = await _make_query(db_session, origin="session")
    query.status = "queued"
    await db_session.commit()
    await _apply(
        db_session,
        Frame(type=FrameType.QUERY_PROGRESS, payload={"query_id": str(query.id)}),
    )
    # The series does not exist until some query records a wait, so absent is 0.
    assert (_value("duckhaven_query_queue_wait_seconds_count", {"replica_id": RID}) or 0) == before


async def test_queue_rejection_counted(db_session):
    before = (
        _value("duckhaven_query_queue_rejected_total", {"replica_id": RID, "reason": "queue_full"})
        or 0
    )
    query = await _make_query(db_session, origin=None)
    await _apply(
        db_session,
        Frame(
            type=FrameType.QUERY_DONE,
            payload={"query_id": str(query.id), "status": "failed", "error": "queue full"},
        ),
    )
    after = _value(
        "duckhaven_query_queue_rejected_total", {"replica_id": RID, "reason": "queue_full"}
    )
    assert after == before + 1


async def test_ordinary_failure_is_not_a_queue_rejection(db_session):
    full = (
        _value("duckhaven_query_queue_rejected_total", {"replica_id": RID, "reason": "queue_full"})
        or 0
    )
    timeout = (
        _value(
            "duckhaven_query_queue_rejected_total", {"replica_id": RID, "reason": "queued_timeout"}
        )
        or 0
    )
    query = await _make_query(db_session, origin=None)
    await _apply(
        db_session,
        Frame(
            type=FrameType.QUERY_DONE,
            payload={
                "query_id": str(query.id),
                "status": "failed",
                "error": "Catalog Error: no such table",
            },
        ),
    )
    assert (
        _value("duckhaven_query_queue_rejected_total", {"replica_id": RID, "reason": "queue_full"})
        or 0
    ) == full
    assert (
        _value(
            "duckhaven_query_queue_rejected_total", {"replica_id": RID, "reason": "queued_timeout"}
        )
        or 0
    ) == timeout


# ── Polaris dependency health ─────────────────────────────────────────────────


def _polaris_client(handler) -> PolarisClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        transport=transport, base_url="http://polaris", headers={"Polaris-Realm": "R"}
    )
    return PolarisClient(
        base_url="http://polaris", realm="R", client_id="root", client_secret="s", http=http
    )


async def test_polaris_request_metrics_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/tokens"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, json={"name": "curated"})

    before = (
        _value(
            "duckhaven_polaris_requests_total",
            {"replica_id": RID, "operation": "get_catalog", "status": "200"},
        )
        or 0
    )
    client = _polaris_client(handler)
    try:
        await client.get_catalog("curated")
    finally:
        await client.aclose()
    assert (
        _value(
            "duckhaven_polaris_requests_total",
            {"replica_id": RID, "operation": "get_catalog", "status": "200"},
        )
        == before + 1
    )
    assert (
        _value(
            "duckhaven_polaris_request_duration_seconds_count",
            {"replica_id": RID, "operation": "get_catalog"},
        )
        is not None
    )
    # The token fetch is recorded under its own operation label.
    assert (
        _value(
            "duckhaven_polaris_requests_total",
            {"replica_id": RID, "operation": "get_token", "status": "200"},
        )
        is not None
    )


async def test_polaris_request_metrics_on_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth/tokens"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        raise httpx.ConnectError("boom")

    before = (
        _value(
            "duckhaven_polaris_requests_total",
            {"replica_id": RID, "operation": "get_catalog", "status": "error"},
        )
        or 0
    )
    client = _polaris_client(handler)
    try:
        with pytest.raises(httpx.ConnectError):
            await client.get_catalog("curated")
    finally:
        await client.aclose()
    assert (
        _value(
            "duckhaven_polaris_requests_total",
            {"replica_id": RID, "operation": "get_catalog", "status": "error"},
        )
        == before + 1
    )


# --- compute wait, parked work and refusals --------------------------------------


def _wait_count(origin: str, outcome: str) -> float:
    return (
        _value(
            "duckhaven_query_compute_wait_seconds_count",
            {"replica_id": RID, "origin": origin, "outcome": outcome},
        )
        or 0.0
    )


def test_a_wait_for_compute_is_observed_by_origin_and_outcome():
    from datetime import timedelta

    from api.metrics import record_compute_wait

    before = _wait_count("scheduled", "bound")
    record_compute_wait("scheduled", "bound", datetime.now(UTC) - timedelta(seconds=30))
    assert _wait_count("scheduled", "bound") == before + 1
    # Even an unlabelled origin is a series: interactive runs have no origin at all.
    before = _wait_count("interactive", "failed")
    record_compute_wait(None, "failed", datetime.now(UTC) - timedelta(seconds=2))
    assert _wait_count("interactive", "failed") == before + 1


def test_a_naive_submission_time_is_read_as_utc():
    from api.metrics import record_compute_wait

    before = _wait_count("describe", "bound")
    record_compute_wait("describe", "bound", datetime.now(UTC).replace(tzinfo=None))
    assert _wait_count("describe", "bound") == before + 1


def test_a_missing_submission_time_observes_nothing():
    from api.metrics import record_compute_wait

    before = _wait_count("elastic", "bound")
    record_compute_wait("elastic", "bound", None)
    assert _wait_count("elastic", "bound") == before


async def test_work_that_waited_for_the_default_to_start_is_timed(db_session, monkeypatch):
    """The wait a cold start puts on interactive work was invisible: the existing
    histogram counts the agent's admission queue and skips every parked origin."""
    from conftest import seed_workspace

    from api.services.compute import service as compute_service

    async def fake_dispatch(db, query, **kwargs):
        return None

    monkeypatch.setattr("api.services.query.dispatch_query", fake_dispatch)
    ws, _ = await seed_workspace(db_session, user_id=uuid.uuid4())
    agent = Agent(
        name="starting",
        status="healthy",
        provider="null",
        lifecycle="running",
        instance_id="dh-metrics",
        capabilities={"duckdb_version": "1.5.5", "extensions": ["httpfs"]},
    )
    db_session.add(agent)
    await db_session.flush()
    db_session.add(
        Query(
            workspace_id=ws.id,
            agent_id=None,
            requested_agent_id=agent.id,
            sql="SELECT 1",
            status="queued",
        )
    )
    await db_session.commit()

    before = _wait_count("interactive", "bound")
    assert await compute_service.bind_targeted_work(db_session, agent) == 1
    assert _wait_count("interactive", "bound") == before + 1


async def test_parked_queries_are_counted_by_kind_with_the_oldest_age(
    client: AsyncClient, db_session
):
    from datetime import timedelta

    from api.metrics import set_reap_leader

    now = datetime.now(UTC)
    for origin, requested, age in (
        ("elastic", None, 120),
        ("elastic", None, 30),
        ("describe", None, 10),
        (None, uuid.uuid4(), 45),
        ("scheduled", None, 5),
    ):
        db_session.add(
            Query(
                workspace_id=uuid.uuid4(),
                agent_id=None,
                requested_agent_id=requested,
                sql="SELECT 1",
                status="queued",
                origin=origin,
                started_at=now - timedelta(seconds=age),
            )
        )
    # Not parked: it has an agent, or it is not queued.
    db_session.add(
        Query(workspace_id=uuid.uuid4(), agent_id=uuid.uuid4(), sql="SELECT 2", status="queued")
    )
    db_session.add(Query(workspace_id=uuid.uuid4(), agent_id=None, sql="SELECT 3", status="done"))
    await db_session.commit()

    set_reap_leader(True)
    try:
        await client.get("/metrics")
    finally:
        set_reap_leader(False)

    assert _value("duckhaven_queries_parked", {"kind": "pool"}) == 3
    assert _value("duckhaven_queries_parked", {"kind": "targeted"}) == 1
    assert _value("duckhaven_queries_parked", {"kind": "scheduled"}) == 1
    assert 119 <= _value("duckhaven_queries_parked_oldest_age_seconds", {"kind": "pool"}) < 180
    assert 44 <= _value("duckhaven_queries_parked_oldest_age_seconds", {"kind": "targeted"}) < 100


async def test_parked_queries_are_reported_by_the_reap_leader_only(client: AsyncClient, db_session):
    """The count is DB-wide; every replica reporting it would multiply it."""
    db_session.add(Query(workspace_id=uuid.uuid4(), agent_id=None, sql="SELECT 1", status="queued"))
    await db_session.commit()

    await client.get("/metrics")  # not the leader
    assert _value("duckhaven_queries_parked", {"kind": "pool"}) is None


def test_the_reapers_stranded_counts_are_exported():
    from api.metrics import record_stranded_failed

    def count(kind: str) -> float:
        return (
            _value("duckhaven_stranded_work_failed_total", {"replica_id": RID, "kind": kind}) or 0.0
        )

    queries, sessions = count("query"), count("session")
    record_stranded_failed({"stranded_queries_failed": 3, "stranded_sessions_failed": 0, "idle": 9})
    assert count("query") == queries + 3
    assert count("session") == sessions  # a zero is not a sample


def test_a_refusal_for_want_of_compute_is_counted_by_reason():
    from api.models.agent import Agent as AgentRow
    from api.services.query import ComputeDecision, Placement, compute_unavailable_error

    def count(reason: str) -> float:
        return (
            _value("duckhaven_compute_unavailable_total", {"replica_id": RID, "reason": reason})
            or 0.0
        )

    before = count("stopped")
    compute_unavailable_error(
        ComputeDecision(
            Placement.UNAVAILABLE,
            AgentRow(id=uuid.uuid4(), name="idle"),
            via_default=True,
            reason="stopped",
        )
    )
    assert count("stopped") == before + 1


async def test_a_refused_parked_run_is_counted_by_cap(db_session, monkeypatch):
    from conftest import seed_workspace

    from api.services.compute import service as compute_service
    from api.services.query import ComputeQueueFull

    def count(scope: str) -> float:
        return (
            _value("duckhaven_parked_queries_rejected_total", {"replica_id": RID, "scope": scope})
            or 0.0
        )

    monkeypatch.setattr(settings, "compute_queue_max_per_workspace", 1)
    monkeypatch.setattr(settings, "compute_queue_max_total", 0)
    ws, _ = await seed_workspace(db_session, user_id=uuid.uuid4())
    db_session.add(Query(workspace_id=ws.id, agent_id=None, sql="SELECT 1", status="queued"))
    await db_session.commit()

    before = count("workspace")
    with pytest.raises(ComputeQueueFull):
        await compute_service.assert_parked_capacity(db_session, ws.id)
    assert count("workspace") == before + 1
    assert count("global") == (count("global"))  # untouched by a workspace refusal
