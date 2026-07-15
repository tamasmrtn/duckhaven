"""Prometheus instrumentation for the control plane.

One scrape target per API replica (``GET /api/metrics``). Persistent counters and
histograms are incremented inline from the request/query code paths; gauges that
reflect instantaneous state (agent utilization, DB pool, maintenance scanner) are
recomputed from scratch on every scrape by a custom collector reading a snapshot
that the endpoint refreshes.

Every per-replica series carries a ``replica_id`` label so a Prometheus
``sum by(...)`` across replicas yields cluster totals without double-counting:
each query completes on, and each HTTP request is served by, exactly one replica.
Agent gauges are emitted only for agents whose control socket this replica owns
(read straight from the in-memory ring buffer), so a connected agent appears under
exactly one replica's scrape. Maintenance-scanner gauges are gated on scanner
leadership so only one replica reports them. See ``docs/operations/monitoring.md``.
"""

import asyncio
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

import sqlalchemy as sa
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from api.config import settings
from api.db.session import engine
from api.models.agent import Agent
from api.models.maintenance import (
    MaintenancePolicy,
    MaintenanceRecommendation,
    TableHealthSample,
)
from api.services.agent_registry import registry

# ── Persistent instruments (incremented inline) ──────────────────────────────

QUERIES_SUBMITTED = Counter(
    "duckhaven_queries_submitted",
    "User queries accepted for dispatch (excludes internal/maintenance queries).",
    ["replica_id"],
)
QUERIES_TOTAL = Counter(
    "duckhaven_queries",
    "User queries reaching a terminal state, by outcome.",
    ["replica_id", "status"],
)
QUERY_DURATION = Histogram(
    "duckhaven_query_duration_seconds",
    "Wall-clock duration of completed user queries.",
    ["replica_id"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300),
)
QUERY_RESULT_BYTES = Histogram(
    "duckhaven_query_result_bytes",
    "Materialized result size of completed user queries.",
    ["replica_id"],
    buckets=(1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10),
)

HTTP_REQUESTS = Counter(
    "duckhaven_http_requests",
    "HTTP requests served by the REST API.",
    ["replica_id", "method", "route", "status"],
)
HTTP_DURATION = Histogram(
    "duckhaven_http_request_duration_seconds",
    "HTTP request latency of the REST API.",
    ["replica_id", "method", "route"],
)

POLARIS_REQUESTS = Counter(
    "duckhaven_polaris_requests",
    "Requests issued to Apache Polaris (Iceberg REST + management APIs).",
    ["replica_id", "operation", "status"],
)
POLARIS_DURATION = Histogram(
    "duckhaven_polaris_request_duration_seconds",
    "Latency of requests issued to Apache Polaris.",
    ["replica_id", "operation"],
)

QUERY_QUEUE_WAIT = Histogram(
    "duckhaven_query_queue_wait_seconds",
    "Time a user query waited in the agent admission queue before running.",
    ["replica_id"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
QUERY_QUEUE_REJECTED = Counter(
    "duckhaven_query_queue_rejected",
    "User queries rejected by agent admission control, by reason.",
    ["replica_id", "reason"],
)

# Agent admission-reject error strings (see agent control/channel.py) -> reason label.
_QUEUE_REJECT_REASONS = {"queue full": "queue_full", "queued timeout": "queued_timeout"}

SQL_SESSIONS_OPENED = Counter(
    "duckhaven_sql_sessions_opened",
    "SQL sessions successfully opened (held-connection sessions for dbt/dlt).",
    ["replica_id"],
)
SQL_SESSIONS_CLOSED = Counter(
    "duckhaven_sql_sessions_closed",
    "SQL sessions closed, by reason "
    "(client/idle/max_lifetime/open_timeout/agent_disconnect/agent_self_reap/failed).",
    ["replica_id", "reason"],
)
SQL_STATEMENTS = Counter(
    "duckhaven_sql_statements",
    "Statements run inside SQL sessions reaching a terminal state, by outcome.",
    ["replica_id", "status"],
)
STATEMENT_POLICY_REJECTIONS = Counter(
    "duckhaven_statement_policy_rejections",
    "Session statements rejected by the capability-scoped policy, by rule.",
    ["replica_id", "rule"],
)


# ── Inline instrumentation helpers (called from the query service) ────────────


def record_query_submitted() -> None:
    QUERIES_SUBMITTED.labels(settings.replica_id).inc()


def record_query_completion(status: str, duration_ms: int | None, result_bytes: int | None) -> None:
    QUERIES_TOTAL.labels(settings.replica_id, status).inc()
    if status != "done":
        return
    if duration_ms is not None:
        QUERY_DURATION.labels(settings.replica_id).observe(duration_ms / 1000.0)
    if result_bytes is not None:
        QUERY_RESULT_BYTES.labels(settings.replica_id).observe(result_bytes)


def record_query_queue_wait(seconds: float) -> None:
    QUERY_QUEUE_WAIT.labels(settings.replica_id).observe(max(0.0, seconds))


def record_query_queue_rejection(error: str | None) -> bool:
    """Count a queue-admission rejection from a failed query's error text.

    Returns True if the error matched a known admission-reject reason.
    """
    reason = _QUEUE_REJECT_REASONS.get((error or "").strip().lower())
    if reason is None:
        return False
    QUERY_QUEUE_REJECTED.labels(settings.replica_id, reason).inc()
    return True


def record_polaris_request(operation: str, status: str, duration_s: float) -> None:
    POLARIS_REQUESTS.labels(settings.replica_id, operation, status).inc()
    POLARIS_DURATION.labels(settings.replica_id, operation).observe(duration_s)


def record_sql_session_opened() -> None:
    SQL_SESSIONS_OPENED.labels(settings.replica_id).inc()


def record_sql_session_closed(reason: str) -> None:
    SQL_SESSIONS_CLOSED.labels(settings.replica_id, reason).inc()


def record_sql_statement(status: str) -> None:
    SQL_STATEMENTS.labels(settings.replica_id, status).inc()


def record_statement_policy_rejection(rule: str) -> None:
    STATEMENT_POLICY_REJECTIONS.labels(settings.replica_id, rule).inc()


# ── Scanner leadership flag (set by the maintenance scanner loop) ─────────────

_scan_leader = False


def set_scan_leader(value: bool) -> None:
    global _scan_leader
    _scan_leader = value


def is_scan_leader() -> bool:
    return _scan_leader


# ── HTTP middleware ───────────────────────────────────────────────────────────


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Record request count and latency, keyed by the matched route template.

    The template (e.g. ``/workspaces/{ws}``) — not the raw URL — is the label so
    cardinality stays bounded. The ``/metrics`` scrape itself is not counted.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", "__unmatched__")
        if template == "/metrics":
            return response
        elapsed = time.perf_counter() - start
        HTTP_REQUESTS.labels(
            settings.replica_id, request.method, template, str(response.status_code)
        ).inc()
        HTTP_DURATION.labels(settings.replica_id, request.method, template).observe(elapsed)
        return response


# ── Scrape-time gauges (recomputed each scrape via a custom collector) ─────────


@dataclass
class _Snapshot:
    """Instantaneous state gathered by the endpoint, read by the collector."""

    agents: list[dict] = field(default_factory=list)
    pool: dict | None = None
    maintenance: dict | None = None
    sql_sessions_active: int = 0


_snapshot = _Snapshot()
_scrape_lock = asyncio.Lock()
_AGENT_LABELS = ["replica_id", "agent_id", "agent_name"]


class _ScrapeCollector:
    """Yields the scrape-time gauge families from the latest snapshot."""

    def collect(self) -> Iterator[GaugeMetricFamily]:
        snap = _snapshot
        yield from _agent_families(snap.agents)
        active = GaugeMetricFamily(
            "duckhaven_sql_sessions_active",
            "Open SQL sessions (held connections); a DB-wide count (use max across replicas).",
        )
        active.add_metric([], snap.sql_sessions_active)
        yield active
        if snap.pool is not None:
            for key, doc in (
                ("size", "Configured connection pool size."),
                ("checked_out", "Connections currently checked out."),
                ("overflow", "Connections beyond the configured pool size."),
            ):
                fam = GaugeMetricFamily(f"duckhaven_db_pool_{key}", doc, labels=["replica_id"])
                fam.add_metric([settings.replica_id], snap.pool[key])
                yield fam
        if snap.maintenance is not None:
            m = snap.maintenance
            last = GaugeMetricFamily(
                "duckhaven_maintenance_last_scan_timestamp_seconds",
                "Unix time of the last completed maintenance scan cycle (scan leader only).",
            )
            if m["last_scan_ts"] is not None:
                last.add_metric([], m["last_scan_ts"])
            yield last
            rec = GaugeMetricFamily(
                "duckhaven_maintenance_open_recommendations",
                "Open maintenance recommendations by severity (scan leader only).",
                labels=["severity"],
            )
            for severity, count in m["open_by_severity"].items():
                rec.add_metric([severity], count)
            yield rec
            samples = GaugeMetricFamily(
                "duckhaven_maintenance_table_health_samples",
                "Total table health samples recorded (scan leader only).",
            )
            samples.add_metric([], m["health_samples"])
            yield samples


def _agent_families(agents: list[dict]) -> Iterator[GaugeMetricFamily]:
    up = GaugeMetricFamily(
        "duckhaven_agent_up", "1 if the agent has a recent sample.", labels=_AGENT_LABELS
    )
    cpu = GaugeMetricFamily(
        "duckhaven_agent_cpu_percent", "Agent CPU utilization.", labels=_AGENT_LABELS
    )
    mem = GaugeMetricFamily(
        "duckhaven_agent_memory_percent", "Agent memory utilization.", labels=_AGENT_LABELS
    )
    running = GaugeMetricFamily(
        "duckhaven_agent_running_queries", "Queries running on the agent.", labels=_AGENT_LABELS
    )
    queued = GaugeMetricFamily(
        "duckhaven_agent_queued_queries", "Queries queued on the agent.", labels=_AGENT_LABELS
    )
    profile = GaugeMetricFamily(
        "duckhaven_agent_active_profile_info",
        "Active concurrency profile of the agent (value is always 1).",
        labels=[*_AGENT_LABELS, "profile"],
    )
    sessions = GaugeMetricFamily(
        "duckhaven_agent_held_sessions",
        "Open SQL sessions holding a connection + admission slot on the agent.",
        labels=_AGENT_LABELS,
    )
    for a in agents:
        base = [settings.replica_id, a["agent_id"], a["agent_name"]]
        up.add_metric(base, 1)
        cpu.add_metric(base, a["cpu_percent"])
        mem.add_metric(base, a["memory_percent"])
        running.add_metric(base, a["running_queries"])
        queued.add_metric(base, a["queued_queries"])
        profile.add_metric([*base, a["active_profile"]], 1)
        sessions.add_metric(base, a["session_count"])
    yield from (up, cpu, mem, running, queued, profile, sessions)


REGISTRY.register(_ScrapeCollector())


async def _collect_agents(db: AsyncSession) -> list[dict]:
    buffers = registry.recent_metrics()  # local sockets only — this replica's owned agents
    if not buffers:
        return []
    ids = [uuid.UUID(aid) for aid in buffers]
    rows = (await db.execute(sa.select(Agent.id, Agent.name).where(Agent.id.in_(ids)))).all()
    names = {str(aid): name for aid, name in rows}
    out: list[dict] = []
    for aid, samples in buffers.items():
        if not samples:
            continue
        latest = samples[-1]
        out.append(
            {
                "agent_id": aid,
                "agent_name": names.get(aid, aid),
                "cpu_percent": latest.get("cpu_percent", 0.0),
                "memory_percent": latest.get("memory_percent", 0.0),
                "running_queries": latest.get("running_queries", 0),
                "queued_queries": latest.get("queued_queries", 0),
                "active_profile": latest.get("active_profile", "auto"),
                "session_count": latest.get("session_count", 0),
            }
        )
    return out


def _collect_pool() -> dict | None:
    try:
        pool = engine.sync_engine.pool
        return {
            "size": pool.size(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
        }
    except Exception:  # noqa: BLE001 — non-QueuePool (e.g. SQLite tests) lacks these
        return None


async def _collect_maintenance(db: AsyncSession) -> dict:
    policy = (await db.execute(sa.select(MaintenancePolicy))).scalars().first()
    last_scan_ts = (
        policy.last_scan_at.timestamp() if policy is not None and policy.last_scan_at else None
    )
    rows = (
        await db.execute(
            sa.select(MaintenanceRecommendation.severity, sa.func.count())
            .where(MaintenanceRecommendation.status == "open")
            .group_by(MaintenanceRecommendation.severity)
        )
    ).all()
    total = (
        await db.execute(sa.select(sa.func.count()).select_from(TableHealthSample))
    ).scalar_one()
    return {
        "last_scan_ts": last_scan_ts,
        "open_by_severity": {severity: count for severity, count in rows},
        "health_samples": total,
    }


async def _collect_sql_sessions_active(db: AsyncSession) -> int:
    from api.models.sql_session import SqlSession

    return (
        await db.execute(
            sa.select(sa.func.count()).select_from(SqlSession).where(SqlSession.status == "open")
        )
    ).scalar_one()


async def render(db: AsyncSession) -> tuple[bytes, str]:
    """Refresh the scrape-time snapshot and return the exposition payload."""
    global _snapshot
    async with _scrape_lock:
        _snapshot = _Snapshot(
            agents=await _collect_agents(db),
            pool=_collect_pool(),
            maintenance=await _collect_maintenance(db) if is_scan_leader() else None,
            sql_sessions_active=await _collect_sql_sessions_active(db),
        )
        return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
