"""The publisher copies audit events, grants and query history exactly, against Postgres.

The audit copy depends on transaction ids and the snapshot horizon, which only
Postgres has, so its guarantees are proved here rather than in the unit suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from api.config import settings
from api.models.agent import Agent
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.lineage import LineageColumnEdge, LineageEdge
from api.models.query import Query
from api.models.storage_backend import StorageBackend
from api.models.user import SERVICE_ACCOUNT_PROVIDER, Credential, User
from api.models.workspace import Workspace, WorkspaceMember
from api.services.audit import record
from api.services.system_catalog import publisher, store

pytestmark = pytest.mark.integration


@pytest.fixture
async def published(system_store):
    """The store, created, and a way to run one tick and read a copied table."""
    assert await store.ensure(system_store)

    async def tick(now: datetime | None = None) -> bool:
        return await publisher.publish_once(system_store, now=now)

    async def rows(table: sa.Table, *order: sa.ColumnElement) -> list[sa.Row]:
        async with store.get_engine().connect() as conn:
            return list((await conn.execute(sa.select(table).order_by(*order))).all())

    return system_store, tick, rows


def _user(email: str, provider: str = "local") -> User:
    return User(id=uuid.uuid4(), email=email, name=email, auth_provider=provider)


async def _member_added(factory, workspace: Workspace, member: User) -> None:
    async with factory() as db:
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=member.id, role="reader"))
        record(
            db,
            "workspace_member.add",
            workspace=workspace,
            subject=member,
            after={"role": "reader"},
        )
        await db.commit()


async def _setup(factory) -> tuple[Workspace, User]:
    workspace = Workspace(id=uuid.uuid4(), slug="sales", name="Sales")
    ana = _user("ana@example.com")
    async with factory() as db:
        db.add_all([workspace, ana])
        await db.commit()
    return workspace, ana


# ── access.audit ──────────────────────────────────────────────────────────────


async def test_an_event_is_copied_whole_and_once(published):
    factory, tick, rows = published
    workspace, ana = await _setup(factory)
    await _member_added(factory, workspace, ana)

    assert await tick() is False
    assert await tick() is False

    (event,) = await rows(store.AUDIT)
    assert (event.category, event.action, event.actor_type) == (
        "access",
        "workspace_member.add",
        "system",
    )
    assert (event.workspace_slug, event.subject_email, event.after) == (
        "sales",
        "ana@example.com",
        {"role": "reader"},
    )


async def test_an_event_committed_late_is_not_skipped(published):
    """A transaction that wrote first and commits last must still be copied."""
    factory, tick, rows = published
    workspace, ana = await _setup(factory)

    async with factory() as slow:
        record(slow, "catalog.create", target_name="slow_catalog")
        await slow.flush()  # takes a transaction id, then holds it open
        await _member_added(factory, workspace, ana)

        await tick()
        # The later, committed event waits behind the open transaction ...
        assert await rows(store.AUDIT) == []
        lag = await publisher.lag_seconds(now=datetime.now(UTC) + timedelta(seconds=30))
        assert lag["access.audit"] >= 30

        await slow.commit()

    await tick()
    # ... and both arrive once it commits, nothing skipped.
    assert sorted(e.action for e in await rows(store.AUDIT)) == [
        "catalog.create",
        "workspace_member.add",
    ]
    assert (await publisher.lag_seconds())["access.audit"] < 5


async def test_a_backlog_larger_than_a_tick_is_copied_across_ticks(published, monkeypatch):
    factory, tick, rows = published
    monkeypatch.setattr(publisher, "BATCH", 2)
    async with factory() as db:
        for name in ("a", "b", "c"):
            record(db, "catalog.create", target_name=name)
        await db.commit()

    assert await tick() is True
    assert len(await rows(store.AUDIT)) == 2
    assert await tick() is False
    assert len(await rows(store.AUDIT)) == 3


# A dump restored into a new Postgres cluster keeps each event's txid, which the new
# cluster's own ids may not reach for weeks; and the store's cursor may come back too.

_NEXT_TXID = sa.text("SELECT pg_snapshot_xmax(pg_current_snapshot())::text::bigint")


async def test_events_restored_from_another_cluster_are_copied(published):
    factory, tick, rows = published
    workspace, ana = await _setup(factory)
    async with factory() as db:
        ahead = await db.scalar(_NEXT_TXID) + 10**9
        record(db, "catalog.create", target_name="restored").txid = ahead
        await db.commit()
    await _member_added(factory, workspace, ana)

    assert await tick() is False

    assert sorted(e.action for e in await rows(store.AUDIT)) == [
        "catalog.create",
        "workspace_member.add",
    ]
    assert (await publisher.lag_seconds())["access.audit"] < 5


async def test_a_cursor_from_another_cluster_starts_the_copy_over(published):
    factory, tick, rows = published
    workspace, ana = await _setup(factory)
    async with store.get_engine().begin() as conn:
        await conn.execute(
            sa.insert(store.CURSORS).values(
                name="audit", value={"txid": 10**15, "id": str(uuid.UUID(int=0))}
            )
        )
    await _member_added(factory, workspace, ana)

    await tick()

    assert [e.action for e in await rows(store.AUDIT)] == ["workspace_member.add"]


async def test_a_restored_event_copied_early_is_not_counted_as_late(published):
    """Once this cluster's ids pass a restored event's, it sits past the cursor
    while already copied; the lag must not report it."""
    factory, tick, rows = published
    async with factory() as slow:
        # The oldest running transaction, so nothing at or above its id is copied yet.
        held = await slow.scalar(sa.text("SELECT pg_current_xact_id()::text::bigint"))
        async with factory() as db:
            restored = record(db, "catalog.create", target_name="restored")
            restored.txid = held
            restored.event_time = datetime.now(UTC) - timedelta(days=30)
            await db.commit()
            copied = {c: getattr(restored, c) for c in (col.name for col in store.AUDIT.columns)}
        async with store.get_engine().begin() as conn:
            await conn.execute(sa.insert(store.AUDIT).values(copied))

        await tick()

        assert (await publisher.lag_seconds())["access.audit"] < 5
        await slow.rollback()


# ── access.grants ─────────────────────────────────────────────────────────────


async def test_grants_follow_an_access_change(published):
    factory, tick, rows = published
    workspace, ana = await _setup(factory)
    robot = _user("robot@service-account.local", SERVICE_ACCOUNT_PROVIDER)
    async with factory() as db:
        db.add(robot)
        await db.commit()
    await _member_added(factory, workspace, ana)
    await tick()

    grants = await rows(store.GRANTS, store.GRANTS.c.kind, store.GRANTS.c.principal_email)
    members = [
        (g.principal_email, g.workspace_slug, g.level)
        for g in grants
        if g.kind == "workspace_member"
    ]
    assert members == [("ana@example.com", "sales", "reader")]
    roles = {g.principal_email: g.principal_type for g in grants if g.kind == "global_role"}
    assert roles["robot@service-account.local"] == "service_account"
    assert roles["ana@example.com"] == "user"

    async with factory() as db:
        await db.execute(sa.delete(WorkspaceMember).where(WorkspaceMember.user_id == ana.id))
        record(db, "workspace_member.remove", workspace=workspace, subject=ana)
        await db.commit()
    await tick()

    assert not [g for g in await rows(store.GRANTS) if g.kind == "workspace_member"]


async def test_grants_are_refreshed_without_an_event_every_few_minutes(published):
    """Deleting an agent removes its grants and records nothing."""
    factory, tick, rows = published
    now = datetime.now(UTC)
    async with factory() as db:
        agent = Agent(id=uuid.uuid4(), name="agent-a")
        db.add(agent)
        await db.commit()
    await tick(now)
    assert [g.agent_name for g in await rows(store.GRANTS) if g.kind == "agent_access_mode"] == [
        "agent-a"
    ]

    async with factory() as db:
        await db.execute(sa.delete(Agent).where(Agent.id == agent.id))
        await db.commit()
    await tick(now + timedelta(minutes=1))
    assert [g.kind for g in await rows(store.GRANTS)].count("agent_access_mode") == 1

    await tick(now + publisher.GRANTS_REFRESH)
    assert "agent_access_mode" not in [g.kind for g in await rows(store.GRANTS)]


# ── query.history ─────────────────────────────────────────────────────────────


def _run(workspace: Workspace, user: User, finished: datetime | None, **fields) -> Query:
    return Query(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        user_id=user.id,
        sql=fields.pop("sql", "SELECT 1;"),
        status=fields.pop("status", "done"),
        started_at=(finished or datetime.now(UTC)) - timedelta(seconds=1),
        finished_at=finished,
        **fields,
    )


async def test_history_copies_the_finished_runs_people_asked_for(published):
    factory, tick, rows = published
    workspace, ana = await _setup(factory)
    now = datetime.now(UTC)
    kept = [
        _run(workspace, ana, now, sql="SELECT 'done';", row_count=3),
        _run(workspace, ana, now, status="failed", error="boom"),
        _run(workspace, ana, now, origin="session"),
    ]
    hidden = [
        _run(workspace, ana, None, status="running"),
        _run(workspace, ana, now, origin="sample"),
        _run(workspace, ana, now, origin="maintenance"),
    ]
    async with factory() as db:
        db.add_all(kept + hidden)
        await db.commit()

    await tick()

    copied = await rows(store.HISTORY)
    assert {r.id for r in copied} == {q.id for q in kept}
    done = next(r for r in copied if r.sql == "SELECT 'done';")
    assert (done.workspace_slug, done.user_email, done.row_count) == ("sales", "ana@example.com", 3)


async def test_a_run_committed_after_a_later_one_is_found_by_the_sweep(published):
    """Another replica's clock, or a slow commit, can put it behind the cursor."""
    factory, tick, rows = published
    workspace, ana = await _setup(factory)
    now = datetime.now(UTC)
    async with factory() as db:
        db.add(_run(workspace, ana, now))
        await db.commit()
    await tick(now)

    late = _run(workspace, ana, now - timedelta(minutes=2))
    async with factory() as db:
        db.add(late)
        await db.commit()
    await tick(now + timedelta(seconds=10))
    assert late.id not in {r.id for r in await rows(store.HISTORY)}

    await tick(now + publisher.HISTORY_SWEEP_EVERY)
    assert late.id in {r.id for r in await rows(store.HISTORY)}


async def test_a_run_that_changes_after_it_was_copied_is_updated(published):
    factory, tick, rows = published
    workspace, ana = await _setup(factory)
    now = datetime.now(UTC)
    run = _run(workspace, ana, now, row_count=1)
    async with factory() as db:
        db.add(run)
        await db.commit()
    await tick(now)

    async with factory() as db:
        await db.execute(sa.update(Query).where(Query.id == run.id).values(row_count=2))
        await db.commit()
    await tick(now + publisher.HISTORY_SWEEP_EVERY)

    assert [r.row_count for r in await rows(store.HISTORY)] == [2]


# ── Retention and lag ─────────────────────────────────────────────────────────


async def test_the_copy_drops_what_retention_drops(published, monkeypatch):
    factory, tick, rows = published
    monkeypatch.setattr(settings, "audit_retention_days", 1)
    monkeypatch.setattr(settings, "system_catalog_query_history_days", 1)
    workspace, ana = await _setup(factory)
    now = datetime.now(UTC)
    async with factory() as db:
        db.add(_run(workspace, ana, now))
        await db.commit()
    await _member_added(factory, workspace, ana)
    await tick(now)
    assert len(await rows(store.AUDIT)) == len(await rows(store.HISTORY)) == 1

    await tick(now + timedelta(days=2))

    assert await rows(store.AUDIT) == []
    assert await rows(store.HISTORY) == []


async def test_each_table_reports_how_far_behind_it_is(published):
    factory, tick, _ = published
    now = datetime.now(UTC)
    await tick(now)

    lag = await publisher.lag_seconds(now=now + timedelta(seconds=40))

    assert lag == {"access.audit": 40.0, "access.grants": 40.0, "query.history": 40.0}


async def test_history_says_where_a_run_ran_and_what_it_cost(published):
    factory, tick, rows = published
    workspace, ana = await _setup(factory)
    now = datetime.now(UTC)
    first = _run(workspace, ana, now - timedelta(seconds=5))
    hit = _run(
        workspace,
        ana,
        now,
        active_catalog="sales",
        active_schema="analytics",
        cache_status="hit",
        result_source_query_id=first.id,
        profile={
            "summary": {
                "cpu_time_ms": 12.6,
                "bytes_read": 4096,
                "peak_memory_bytes": 2048,
                "spill_bytes": 0,
            }
        },
    )
    async with factory() as db:
        db.add_all([first, hit])
        await db.commit()

    await tick()

    (copied,) = [r for r in await rows(store.HISTORY) if r.id == hit.id]
    assert (copied.active_catalog, copied.active_schema) == ("sales", "analytics")
    assert (copied.cache_status, copied.result_source_query_id) == ("hit", first.id)
    assert (
        copied.cpu_time_ms,
        copied.bytes_read,
        copied.peak_memory_bytes,
        copied.spill_bytes,
        # Rounded to whole milliseconds.
    ) == (13, 4096, 2048, 0)


async def _sales_catalog(factory, workspace: Workspace, owner: User) -> Catalog:
    backend = StorageBackend(kind="object_store", name="b", root_uri="", created_by=owner.id)
    catalog = Catalog(
        id=uuid.uuid4(), slug="sales", name="sales", polaris_name="sales", created_by=owner.id
    )
    catalog.storage_backend = backend
    async with factory() as db:
        db.add_all([backend, catalog])
        await db.flush()
        db.add(WorkspaceCatalog(workspace_id=workspace.id, catalog_id=catalog.id, is_default=True))
        await db.commit()
    return catalog


async def test_each_table_a_run_read_and_wrote_is_recorded(published):
    factory, tick, rows = published
    workspace, ana = await _setup(factory)
    catalog = await _sales_catalog(factory, workspace, ana)
    now = datetime.now(UTC)
    insert = _run(
        workspace,
        ana,
        now,
        sql="INSERT INTO sales.analytics.daily SELECT * FROM orders;",
        active_catalog="sales",
        active_schema="analytics",
    )
    # No context recorded: the workspace's default catalog and schema.
    unplaced = _run(workspace, ana, now, sql="SELECT * FROM customers;")
    failed = _run(workspace, ana, now, sql="SELECT * FROM refunds;", status="failed")
    async with factory() as db:
        db.add_all([insert, unplaced, failed])
        await db.commit()

    await tick()

    recorded = {
        (r.query_id, r.catalog_id, r.schema_name, r.table_name, r.access, r.user_email)
        for r in await rows(store.TABLE_ACCESS)
    }
    assert recorded == {
        (insert.id, catalog.id, "analytics", "daily", "write", "ana@example.com"),
        (insert.id, catalog.id, "analytics", "orders", "read", "ana@example.com"),
        (unplaced.id, catalog.id, "analytics", "customers", "read", "ana@example.com"),
    }


async def test_runs_copied_before_history_gained_columns_are_copied_again(published):
    factory, tick, rows = published
    workspace, ana = await _setup(factory)
    await _sales_catalog(factory, workspace, ana)
    now = datetime.now(UTC)
    run = _run(workspace, ana, now, sql="SELECT * FROM orders;", cache_status="miss")
    async with factory() as db:
        db.add(run)
        await db.commit()
    await tick(now)
    # As an older API left it: no cache_status, no table access, an older cursor.
    async with store.get_engine().begin() as conn:
        await conn.execute(sa.update(store.HISTORY).values(cache_status=None))
        await conn.execute(sa.delete(store.TABLE_ACCESS))
        cursor = await conn.scalar(
            sa.select(store.CURSORS.c.value).where(store.CURSORS.c.name == "history")
        )
        await conn.execute(
            sa.update(store.CURSORS)
            .where(store.CURSORS.c.name == "history")
            .values(value={k: v for k, v in cursor.items() if k != "version"})
        )

    # A run finished meanwhile is copied in the same tick, not after the old ones.
    new = _run(workspace, ana, now + timedelta(seconds=5), sql="SELECT * FROM customers;")
    async with factory() as db:
        db.add(new)
        await db.commit()

    await tick(now + timedelta(seconds=10))

    assert {r.id: r.cache_status for r in await rows(store.HISTORY)} == {
        run.id: "miss",
        new.id: None,
    }
    assert sorted(r.table_name for r in await rows(store.TABLE_ACCESS)) == ["customers", "orders"]
    cursor = await _cursor()
    assert (cursor["version"], cursor["rewrite_through"]) == (publisher.HISTORY_VERSION, None)


async def _cursor() -> dict:
    async with store.get_engine().connect() as conn:
        return await conn.scalar(
            sa.select(store.CURSORS.c.value).where(store.CURSORS.c.name == "history")
        )


async def test_users_workspaces_and_tokens_are_copied_without_secrets(published):
    factory, tick, rows = published
    workspace, ana = await _setup(factory)
    robot = _user("robot@example.com", SERVICE_ACCOUNT_PROVIDER)
    expires = datetime(2027, 1, 1, tzinfo=UTC)
    pat = Credential(user=ana, kind="pat", token_hash="a" * 64, expires_at=expires)
    async with factory() as db:
        db.add(robot)
        await db.flush()
        db.add_all(
            [
                pat,
                Credential(user_id=ana.id, kind="session", token="s3cret"),
                # The assistant's per-turn token.
                Credential(
                    user_id=robot.id, on_behalf_of_id=ana.id, kind="pat", token_hash="b" * 64
                ),
            ]
        )
        await db.commit()

    await tick()

    users = {r.email: r for r in await rows(store.USERS)}
    assert (users["ana@example.com"].principal_type, users["ana@example.com"].global_role) == (
        "user",
        "user",
    )
    assert users["robot@example.com"].principal_type == "service_account"
    (copied,) = await rows(store.WORKSPACES)
    assert (copied.workspace_id, copied.workspace_slug, copied.name) == (
        workspace.id,
        "sales",
        "Sales",
    )
    (token,) = await rows(store.CREDENTIALS)
    assert (token.credential_id, token.kind, token.principal_email, token.expires_at) == (
        pat.id,
        "pat",
        "ana@example.com",
        expires,
    )
    assert not {"token", "token_hash"} & set(store.CREDENTIALS.c.keys())


async def test_lineage_is_replaced_every_few_minutes(published):
    factory, tick, rows = published
    workspace, ana = await _setup(factory)
    catalog = await _sales_catalog(factory, workspace, ana)
    edge = LineageEdge(
        source_key="s",
        source_catalog_id=catalog.id,
        source_schema="analytics",
        source_table="orders",
        target_key="t",
        source_system=None,
        target_catalog_id=catalog.id,
        target_schema="analytics",
        target_table="daily",
        provider="execution",
        operation="insert",
        workspace_id=workspace.id,
    )
    async with factory() as db:
        db.add(edge)
        await db.flush()
        db.add(LineageColumnEdge(edge_id=edge.id, source_column="total", target_column="revenue"))
        await db.commit()
    now = datetime.now(UTC)

    await tick(now)

    (copied,) = await rows(store.TABLE_LINEAGE)
    assert (copied.source_catalog_name, copied.source_table, copied.target_table) == (
        "sales",
        "orders",
        "daily",
    )
    assert (copied.workspace_slug, copied.operation, copied.column_lineage) == (
        "sales",
        "insert",
        "unknown",
    )
    (column,) = await rows(store.COLUMN_LINEAGE)
    assert (column.source_table, column.source_column, column.target_column) == (
        "orders",
        "total",
        "revenue",
    )

    async with factory() as db:
        await db.execute(sa.delete(LineageEdge))
        await db.commit()
    await tick(now + timedelta(seconds=10))
    assert len(await rows(store.TABLE_LINEAGE)) == 1
    await tick(now + publisher.LINEAGE_REFRESH)
    assert await rows(store.TABLE_LINEAGE) == []
    assert await rows(store.COLUMN_LINEAGE) == []
