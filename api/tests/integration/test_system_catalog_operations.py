"""The system catalog's compute, jobs, query.sessions and storage tables, as the
publisher copies them from the duckhaven database.

Needs the integration Postgres (``DATABASE_URL``); ``system_store`` gives each test
an empty store.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from api.config import settings
from api.models.agent import Agent, AgentLifecycleEvent, AgentMetricsMinute
from api.models.assistant import AssistantConversation, AssistantMessage, AssistantToolCall
from api.models.catalog import Catalog
from api.models.maintenance import MaintenanceRecommendation, TableHealthSample
from api.models.query import Query, SavedQuery, Schedule
from api.models.semantic import SemanticDataset, SemanticDimension, SemanticMetric, SemanticModel
from api.models.sql_session import SqlSession
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace
from api.services.system_catalog import publisher, store

pytestmark = pytest.mark.integration


@pytest.fixture
async def published(system_store):
    """The store, created, and a way to run one tick and read a copied table."""
    assert await store.ensure(system_store)

    async def tick(now: datetime) -> bool:
        return await publisher.publish_once(system_store, now=now)

    async def rows(table: sa.Table) -> list[sa.Row]:
        async with store.get_engine().connect() as conn:
            return list((await conn.execute(sa.select(table))).all())

    return system_store, tick, rows


async def _setup(factory) -> tuple[Workspace, User, Agent]:
    workspace = Workspace(id=uuid.uuid4(), slug="sales", name="Sales", timezone="Europe/Paris")
    ana = User(id=uuid.uuid4(), email="ana@example.com", name="Ana", auth_provider="local")
    agent = Agent(id=uuid.uuid4(), name="etl", status="healthy", capabilities={"cores": 4})
    async with factory() as db:
        db.add_all([workspace, ana, agent])
        await db.commit()
    return workspace, ana, agent


async def _add(factory, *objects) -> None:
    async with factory() as db:
        db.add_all(objects)
        await db.commit()


def _minute(agent: Agent, minute: datetime, cpu: float) -> AgentMetricsMinute:
    return AgentMetricsMinute(
        agent_id=agent.id,
        minute=minute,
        cpu_avg=cpu,
        cpu_max=cpu,
        mem_avg=40.0,
        mem_max=55.0,
        running_max=2,
        queued_max=0,
        session_max=1,
        sample_count=30,
    )


async def test_what_an_agent_did_outlives_the_agent(published):
    factory, tick, rows = published
    _, _, agent = await _setup(factory)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    await _add(
        factory,
        AgentLifecycleEvent(agent_id=agent.id, event="connected", at=now - timedelta(minutes=2)),
        _minute(agent, now - timedelta(minutes=1), 37.5),
    )

    await tick(now)

    (copied,) = await rows(store.AGENTS)
    assert (copied.name, copied.kind, copied.status, copied.cores) == (
        "etl",
        "static",
        "healthy",
        4,
    )
    assert [(e.agent_name, e.event) for e in await rows(store.AGENT_EVENTS)] == [
        ("etl", "connected")
    ]
    (minute,) = await rows(store.AGENT_UTILIZATION)
    assert (minute.agent_name, minute.cpu_avg_percent, minute.sessions_max) == ("etl", 37.5, 1)

    async with factory() as db:
        await db.execute(sa.delete(Agent).where(Agent.id == agent.id))
        await db.commit()
    await tick(now + publisher.SNAPSHOT_REFRESH)

    assert await rows(store.AGENTS) == []
    assert len(await rows(store.AGENT_EVENTS)) == 1
    assert len(await rows(store.AGENT_UTILIZATION)) == 1


async def test_a_minute_merged_after_it_was_copied_is_updated(published):
    """A second replica can flush into a minute already copied."""
    factory, tick, rows = published
    _, _, agent = await _setup(factory)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    minute = now - timedelta(minutes=1)
    await _add(factory, _minute(agent, minute, 10.0))
    await tick(now)

    async with factory() as db:
        await db.execute(
            sa.update(AgentMetricsMinute)
            .where(AgentMetricsMinute.agent_id == agent.id)
            .values(cpu_avg=20.0)
        )
        await db.commit()
    await tick(now + publisher.HISTORY_SWEEP_EVERY)

    assert [m.cpu_avg_percent for m in await rows(store.AGENT_UTILIZATION)] == [20.0]


async def test_a_session_is_followed_until_it_ends(published):
    factory, tick, rows = published
    workspace, ana, agent = await _setup(factory)
    now = datetime.now(UTC)
    session = SqlSession(
        workspace_id=workspace.id,
        user_id=ana.id,
        agent_id=agent.id,
        status="open",
        client_name="dbt-duckhaven",
        created_at=now - timedelta(seconds=30),
        opened_at=now - timedelta(seconds=29),
    )
    await _add(factory, session)
    await tick(now)
    (copied,) = await rows(store.SESSIONS)
    assert (copied.status, copied.user_email, copied.agent_name) == (
        "open",
        "ana@example.com",
        "etl",
    )

    # Long after the late window: only following a live session finds its end.
    later = now + timedelta(hours=3)
    async with factory() as db:
        await db.execute(
            sa.update(SqlSession)
            .where(SqlSession.id == session.id)
            .values(status="closed", close_reason="client", closed_at=later)
        )
        await db.commit()
    await tick(later)

    (copied,) = await rows(store.SESSIONS)
    assert (copied.status, copied.close_reason, copied.closed_at) == ("closed", "client", later)


async def test_schedules_are_replaced_every_minute(published):
    factory, tick, rows = published
    workspace, ana, agent = await _setup(factory)
    saved = SavedQuery(
        workspace_id=workspace.id,
        name="Daily revenue",
        sql="SELECT 1;",
        created_by=ana.id,
        updated_by=ana.id,
    )
    await _add(factory, saved)
    now = datetime.now(UTC)
    run = Query(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        user_id=ana.id,
        sql="SELECT 1;",
        status="failed",
        started_at=now - timedelta(minutes=5),
        finished_at=now - timedelta(minutes=4),
    )
    schedule = Schedule(
        workspace_id=workspace.id,
        saved_query_id=saved.id,
        agent_id=agent.id,
        cron="0 6 * * *",
        enabled=True,
        last_run_query_id=run.id,
        created_by=ana.id,
    )
    await _add(factory, run, schedule)

    await tick(now)

    (copied,) = await rows(store.SCHEDULES)
    assert (copied.saved_query_name, copied.agent_name, copied.timezone) == (
        "Daily revenue",
        "etl",
        "Europe/Paris",
    )
    assert (copied.last_run_status, copied.created_by_email, copied.enabled) == (
        "failed",
        "ana@example.com",
        True,
    )

    async with factory() as db:
        await db.execute(sa.update(Schedule).values(enabled=False))
        await db.commit()
    await tick(now + timedelta(seconds=10))
    assert [s.enabled for s in await rows(store.SCHEDULES)] == [True]
    await tick(now + publisher.SNAPSHOT_REFRESH)
    assert [s.enabled for s in await rows(store.SCHEDULES)] == [False]


async def _catalog(factory, owner: User) -> Catalog:
    backend = StorageBackend(kind="object_store", name="b", root_uri="", created_by=owner.id)
    catalog = Catalog(
        id=uuid.uuid4(), slug="sales", name="sales", polaris_name="sales", created_by=owner.id
    )
    catalog.storage_backend = backend
    await _add(factory, backend, catalog)
    return catalog


async def test_each_maintenance_run_is_kept_after_the_next_replaces_it(published):
    """The source keeps only a recommendation's latest run."""
    factory, tick, rows = published
    workspace, ana, _ = await _setup(factory)
    catalog = await _catalog(factory, ana)
    now = datetime.now(UTC)
    first, second = uuid.uuid4(), uuid.uuid4()
    recommendation = MaintenanceRecommendation(
        workspace_id=workspace.id,
        catalog_id=catalog.id,
        schema_name="analytics",
        table_name="orders",
        kind="compact_small_files",
        severity="warning",
        confidence="high",
        rationale="Many small files",
        apply_status="succeeded",
        apply_result={"before": {"files": 900}, "after": {"files": 12}},
        applied_at=now - timedelta(hours=1),
        applied_by=ana.id,
        applied_query_id=first,
    )
    sample = TableHealthSample(
        workspace_id=workspace.id,
        catalog_id=catalog.id,
        schema_name="analytics",
        table_name="orders",
        scanned_at=now - timedelta(minutes=5),
        score=62,
        data_file_count=900,
    )
    await _add(factory, recommendation, sample)
    await tick(now)

    async with factory() as db:
        await db.execute(
            sa.update(MaintenanceRecommendation).values(
                apply_status="running", apply_result=None, applied_at=now, applied_query_id=second
            )
        )
        await db.commit()
    await tick(now + timedelta(seconds=10))

    runs = {r.query_id: r for r in await rows(store.MAINTENANCE_OPERATIONS)}
    assert (runs[first].status, runs[first].result["after"]) == ("succeeded", {"files": 12})
    assert (runs[second].status, runs[second].applied_by_email) == ("running", "ana@example.com")
    assert {r.catalog_name for r in runs.values()} == {"sales"}
    (health,) = await rows(store.TABLE_HEALTH)
    assert (health.catalog_name, health.table_name, health.score) == ("sales", "orders", 62)


async def test_the_copy_drops_what_its_retention_drops(published, monkeypatch):
    factory, tick, rows = published
    _, _, agent = await _setup(factory)
    now = datetime.now(UTC)
    await _add(
        factory,
        AgentLifecycleEvent(agent_id=agent.id, event="connected", at=now - timedelta(days=10)),
        AgentLifecycleEvent(agent_id=agent.id, event="disconnected", at=now - timedelta(days=1)),
    )
    await tick(now)
    assert len(await rows(store.AGENT_EVENTS)) == 2

    monkeypatch.setattr(settings, "system_catalog_retention_days", 5)
    await tick(now + publisher.PURGE_EVERY)

    assert [e.event for e in await rows(store.AGENT_EVENTS)] == ["disconnected"]


async def test_assistant_usage_is_copied_without_what_was_said(published):
    factory, tick, rows = published
    workspace, ana, _ = await _setup(factory)
    robot = User(
        id=uuid.uuid4(),
        email="assistant@example.com",
        name="Assistant",
        auth_provider="service_account",
    )
    conversation = AssistantConversation(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        user_id=ana.id,
        service_account_id=robot.id,
        title="Revenue by region",
    )
    await _add(factory, robot)
    await _add(factory, conversation)
    await _add(
        factory,
        AssistantMessage(
            conversation_id=conversation.id,
            ordinal=0,
            payload=[{"parts": [{"content": "secret question"}]}],
            input_tokens=1200,
            output_tokens=300,
        ),
        AssistantToolCall(
            conversation_id=conversation.id,
            tool="run_sql",
            args={"sql": "SELECT secret FROM t;"},
            status="denied",
            detail="no grant on t",
            latency_ms=40,
        ),
    )
    now = datetime.now(UTC)
    await tick(now)

    (usage,) = await rows(store.ASSISTANT_USAGE)
    assert (usage.user_email, usage.service_account_email, usage.turn) == (
        "ana@example.com",
        "assistant@example.com",
        0,
    )
    assert (usage.input_tokens, usage.output_tokens, usage.workspace_slug) == (1200, 300, "sales")
    (call,) = await rows(store.TOOL_CALLS)
    assert (call.tool, call.status, call.latency_ms) == ("run_sql", "denied", 40)
    copied = {c.name for t in (store.ASSISTANT_USAGE, store.TOOL_CALLS) for c in t.columns}
    assert not {"payload", "args", "detail", "tables", "title"} & copied

    # Deleting the conversation keeps what it cost.
    async with factory() as db:
        await db.execute(sa.delete(AssistantConversation))
        await db.commit()
    await tick(now + timedelta(seconds=10))
    assert len(await rows(store.ASSISTANT_USAGE)) == 1


async def test_storage_backends_are_copied_without_their_credentials(published):
    factory, tick, rows = published
    _, ana, _ = await _setup(factory)
    await _catalog(factory, ana)
    await _add(
        factory,
        StorageBackend(
            kind="s3",
            name="lake",
            root_uri="s3://bucket/lake",
            config={"role_arn": "arn:aws:iam::1:role/x", "external_id": "e"},
            created_by=ana.id,
        ),
    )

    await tick(datetime.now(UTC))

    assert {
        (b.name, b.kind, b.root_uri, b.catalog_count, b.created_by_email)
        for b in await rows(store.STORAGE_BACKENDS)
    } == {
        ("b", "object_store", "", 1, "ana@example.com"),
        ("lake", "s3", "s3://bucket/lake", 0, "ana@example.com"),
    }
    assert "config" not in store.STORAGE_BACKENDS.c


async def test_semantic_models_are_copied_with_the_tables_their_filter_checks(published):
    factory, tick, rows = published
    workspace, ana, _ = await _setup(factory)
    catalog = await _catalog(factory, ana)
    model = SemanticModel(
        id=uuid.uuid4(), workspace_id=workspace.id, slug="revenue", name="Revenue", owner_id=ana.id
    )
    await _add(factory, model)
    dataset = SemanticDataset(
        id=uuid.uuid4(),
        model_id=model.id,
        name="orders",
        catalog_id=catalog.id,
        schema_name="analytics",
        table_name="orders",
    )
    await _add(factory, dataset)
    await _add(
        factory,
        SemanticDimension(
            model_id=model.id,
            dataset_id=dataset.id,
            name="region",
            kind="categorical",
            expr="region",
            sample_values=["EMEA", "APAC"],
        ),
        SemanticMetric(
            model_id=model.id, dataset_id=dataset.id, name="revenue", agg="sum", expr="total"
        ),
    )

    await tick(datetime.now(UTC))

    (copied,) = await rows(store.SEMANTIC_MODELS)
    assert (copied.model_slug, copied.status, copied.owner_email) == (
        "revenue",
        "draft",
        "ana@example.com",
    )
    (ds,) = await rows(store.SEMANTIC_DATASETS)
    assert (ds.catalog_name, ds.schema_name, ds.table_name) == ("sales", "analytics", "orders")
    (dimension,) = await rows(store.SEMANTIC_DIMENSIONS)
    assert (dimension.dataset_name, dimension.model_slug) == ("orders", "revenue")
    assert "sample_values" not in store.SEMANTIC_DIMENSIONS.c
    (metric,) = await rows(store.SEMANTIC_METRICS)
    assert (metric.name, metric.agg, metric.dataset_name) == ("revenue", "sum", "orders")
    assert [
        (t.model_id, t.catalog_id, t.table_name) for t in await rows(store.ACL_SEMANTIC_TABLES)
    ] == [(model.id, catalog.id, "orders")]
