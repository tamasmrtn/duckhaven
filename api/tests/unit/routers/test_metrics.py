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
    q = Query(
        workspace_id=uuid.uuid4(),
        sql="SELECT 1",
        status="running",
        origin=origin,
        started_at=datetime.now(UTC),
    )
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return q


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
    await query_service.handle_agent_frame(db_session, await _done_frame(query.id))

    assert _value("duckhaven_queries_total", {"replica_id": RID, "status": "done"}) == before + 1
    assert _value("duckhaven_query_duration_seconds_count", {"replica_id": RID}) == dur_before + 1
    assert _value("duckhaven_query_result_bytes_count", {"replica_id": RID}) == bytes_before + 1


async def test_failed_query_counts_but_skips_histograms(db_session):
    before = _value("duckhaven_queries_total", {"replica_id": RID, "status": "failed"}) or 0
    dur_before = _value("duckhaven_query_duration_seconds_count", {"replica_id": RID}) or 0

    query = await _make_query(db_session, origin=None)
    await query_service.handle_agent_frame(db_session, await _done_frame(query.id, status="failed"))

    assert _value("duckhaven_queries_total", {"replica_id": RID, "status": "failed"}) == before + 1
    # Duration/result histograms only observe successful queries.
    assert _value("duckhaven_query_duration_seconds_count", {"replica_id": RID}) == dur_before


async def test_internal_queries_excluded(db_session):
    # Both sides are coerced the same way. A counter no test in this worker has
    # touched yet reads back as None rather than 0, so coercing only `before`
    # made this compare None to 0 and fail purely on which tests xdist happened
    # to put alongside it.
    label = {"replica_id": RID, "status": "done"}
    before = _value("duckhaven_queries_total", label) or 0
    query = await _make_query(db_session, origin="maintenance")
    await query_service.handle_agent_frame(db_session, await _done_frame(query.id))
    assert (_value("duckhaven_queries_total", label) or 0) == before


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
    val = _value("duckhaven_db_pool_size", {"replica_id": RID})
    if isinstance(engine.sync_engine.pool, QueuePool):
        assert val == settings.db_pool_size
    else:  # NullPool / SQLite-style pools don't expose sizing stats
        assert val is None


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
    await query_service.handle_agent_frame(
        db_session,
        Frame(type=FrameType.QUERY_PROGRESS, payload={"query_id": str(query.id), "stage": "scan"}),
    )
    assert _value("duckhaven_query_queue_wait_seconds_count", {"replica_id": RID}) == before + 1


async def test_queue_rejection_counted(db_session):
    before = (
        _value("duckhaven_query_queue_rejected_total", {"replica_id": RID, "reason": "queue_full"})
        or 0
    )
    query = await _make_query(db_session, origin=None)
    await query_service.handle_agent_frame(
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
    await query_service.handle_agent_frame(
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
