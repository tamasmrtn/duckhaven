"""Scoped-mode grant enforcement at SQL dispatch (issue #129)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from conftest import seed_workspace
from httpx import AsyncClient
from sqlalchemy import update

from api.models.agent import Agent
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.services import grants as grant_service
from api.services import sql_metadata
from api.services.agent_registry import registry
from api.services.auth import hash_password
from api.services.maintenance.verbs import APPLICABLE_KINDS, render


class MockWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest_asyncio.fixture
async def user(db_session):
    u = User(email="q@grants.local", password_hash=hash_password("pw"), name="Q", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest_asyncio.fixture
async def authed_client(client: AsyncClient, user: User):
    await client.post("/auth/login", json={"email": "q@grants.local", "password": "pw"})
    return client


@pytest_asyncio.fixture
async def connected_agent(db_session):
    a = Agent(
        name="test-agent",
        status="healthy",
        capabilities={"duckdb_version": "1.5.5", "extensions": ["httpfs"]},
    )
    db_session.add(a)
    await db_session.commit()
    await db_session.refresh(a)
    mock_ws = MockWebSocket()
    registry.register(a.id, mock_ws)  # type: ignore[arg-type]
    yield a
    registry.unregister(a.id)


@pytest_asyncio.fixture
async def scoped_ws(db_session, user: User):
    """A workspace (user is owner) whose one catalog is in scoped mode."""
    ws, cat = await seed_workspace(db_session, user_id=user.id)
    await db_session.execute(
        update(WorkspaceCatalog)
        .where(WorkspaceCatalog.workspace_id == ws.id, WorkspaceCatalog.catalog_id == cat.id)
        .values(access_mode="scoped")
    )
    await db_session.commit()
    return ws, cat


@pytest_asyncio.fixture
async def mixed_ws(db_session, user: User):
    """A workspace holding one `open` catalog (the default) and one `scoped` one.

    The shape issue #177 is about: the session's active catalog is open, but the
    worksheet still attaches the scoped catalog alongside it."""
    ws, open_cat = await seed_workspace(db_session, user_id=user.id, slug="mixed-ws")
    backend = StorageBackend(
        kind="object_store", name="mixed-ws-scoped-store", root_uri="/tmp/test", created_by=user.id
    )
    db_session.add(backend)
    await db_session.flush()
    scoped_cat = Catalog(
        slug="sales",
        name="Sales",
        polaris_name="mixed-ws-sales",
        storage_backend_id=backend.id,
        created_by=user.id,
    )
    db_session.add(scoped_cat)
    await db_session.flush()
    db_session.add(
        WorkspaceCatalog(
            workspace_id=ws.id,
            catalog_id=scoped_cat.id,
            is_default=False,
            access_mode="scoped",
            attached_by=user.id,
        )
    )
    await db_session.commit()
    await db_session.refresh(scoped_cat)
    return ws, open_cat, scoped_cat


def _grant(db_session, user, cat, tier, schema="analytics", table=None):
    db_session.add(
        CatalogGrant(
            user_id=user.id, catalog_id=cat.id, schema_name=schema, table_name=table, tier=tier
        )
    )


async def _run(authed_client, ws, agent, sql):
    return await authed_client.post(
        f"/workspaces/{ws.slug}/queries", json={"sql": sql, "agent_id": str(agent.id)}
    )


async def test_scoped_dispatch_denied_without_grant(
    authed_client, scoped_ws, connected_agent, db_session
):
    ws, _cat = scoped_ws
    resp = await _run(authed_client, ws, connected_agent, "SELECT * FROM analytics.leads")
    assert resp.status_code == 403
    assert resp.json()["error"] == "grant_denied"


async def test_scoped_dispatch_allowed_with_reader_grant(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="leads")
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, "SELECT * FROM analytics.leads")
    assert resp.status_code == 202


async def test_join_denied_if_any_table_lacks_reader(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="a")  # but not `b`
    await db_session.commit()
    sql = "SELECT * FROM analytics.a JOIN analytics.b ON a.id = b.id"
    resp = await _run(authed_client, ws, connected_agent, sql)
    assert resp.status_code == 403


async def test_write_requires_writer_on_target(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="t")  # reader is not enough to write
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, "INSERT INTO analytics.t VALUES (1)")
    assert resp.status_code == 403

    await db_session.execute(
        update(CatalogGrant)
        .where(CatalogGrant.catalog_id == cat.id, CatalogGrant.table_name == "t")
        .values(tier="writer")
    )
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, "INSERT INTO analytics.t VALUES (1)")
    assert resp.status_code == 202


async def test_truncate_requires_writer_on_target(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    # TRUNCATE destroys every row, so `reader` must not be enough. DuckDB parses
    # it as a DELETE, so the one-shot allowlist admits it — the grant check is
    # the only thing standing between a reader and an empty table.
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="t")
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, "TRUNCATE TABLE analytics.t")
    assert resp.status_code == 403

    await db_session.execute(
        update(CatalogGrant)
        .where(CatalogGrant.catalog_id == cat.id, CatalogGrant.table_name == "t")
        .values(tier="writer")
    )
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, "TRUNCATE TABLE analytics.t")
    assert resp.status_code == 202


async def test_alter_requires_writer_on_target(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="t")
    await db_session.commit()
    sql = "ALTER TABLE analytics.t ADD COLUMN c INTEGER"
    resp = await _run(authed_client, ws, connected_agent, sql)
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM system.information_schema.tables",
        "SELECT * FROM duckdb_tables()",
        "SHOW TABLES",
        "PRAGMA table_info('analytics.leads')",
    ],
)
async def test_metadata_enumeration_denied_in_scoped_catalog(
    authed_client, scoped_ws, connected_agent, db_session, sql
):
    """A principal with no grant must not be able to learn that a table exists.

    The browse endpoints filter their listings by grant; DuckDB computes these
    views across every attached catalog and cannot, so they are rejected instead.
    """
    ws, _cat = scoped_ws
    resp = await _run(authed_client, ws, connected_agent, sql)
    assert resp.status_code == 403
    assert resp.json()["error"] == "grant_denied"


async def test_metadata_enumeration_denied_even_with_a_grant(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    """Holding a grant on one table does not re-open the unfiltered listing —
    it would still enumerate every other table in the catalog."""
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="leads")
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, "SELECT * FROM information_schema.tables")
    assert resp.status_code == 403


async def test_info_schema_still_exempt_in_open_catalog(
    authed_client, db_session, user, connected_agent
):
    """The rejection is scoped-only: an `open` attachment (the default) keeps
    today's behavior, so no existing workspace loses `information_schema`."""
    ws, _cat = await seed_workspace(db_session, user_id=user.id, slug="open-info-ws")
    resp = await _run(authed_client, ws, connected_agent, "SELECT * FROM information_schema.tables")
    assert resp.status_code == 202


async def test_enumeration_denied_from_the_open_catalog_of_a_mixed_workspace(
    authed_client, mixed_ws, connected_agent
):
    """Deliberately workspace-wide (issue #177): the worksheet attaches every
    catalog the workspace binds, so this listing would return the scoped
    catalog's objects even though the active catalog is open. The reason names
    the scoped catalog so the denial is explicable from the open one."""
    ws, _open_cat, scoped_cat = mixed_ws
    resp = await _run(authed_client, ws, connected_agent, "SELECT * FROM information_schema.tables")
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"] == "grant_denied"
    assert scoped_cat.slug in body["message"]


async def test_open_catalog_of_a_mixed_workspace_still_queryable(
    authed_client, mixed_ws, connected_agent
):
    """Only the unfilterable listings go away — ordinary access to the open
    catalog is untouched by the neighbouring scoped attachment."""
    ws, open_cat, _scoped_cat = mixed_ws
    resp = await _run(
        authed_client, ws, connected_agent, f"SELECT * FROM {open_cat.slug}.analytics.leads"
    )
    assert resp.status_code == 202


async def test_describe_needs_only_metadata_tier(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    """DESCRIBE is the supported column path for Iceberg relations, and it reads
    no rows — so the discovery tier that can already see the columns through the
    browse endpoint can run it in SQL too."""
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "metadata", table="leads")
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, "DESCRIBE analytics.leads")
    assert resp.status_code == 202
    resp = await _run(
        authed_client, ws, connected_agent, "SELECT * FROM (DESCRIBE analytics.leads)"
    )
    assert resp.status_code == 202
    # The same tier still cannot read the rows.
    resp = await _run(authed_client, ws, connected_agent, "SELECT * FROM analytics.leads")
    assert resp.status_code == 403


async def test_describe_denied_without_any_grant(
    authed_client, scoped_ws, connected_agent, db_session
):
    ws, _cat = scoped_ws
    resp = await _run(authed_client, ws, connected_agent, "DESCRIBE analytics.leads")
    assert resp.status_code == 403


async def test_summarize_still_needs_reader(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    """SUMMARIZE scans the rows to compute its statistics, so the metadata tier
    must not reach it even though its output looks like a describe."""
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "metadata", table="leads")
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, "SUMMARIZE analytics.leads")
    assert resp.status_code == 403


async def test_open_mode_dispatch_needs_no_grant(authed_client, db_session, user, connected_agent):
    # A catalog left in default `open` mode dispatches without any grant.
    ws, _cat = await seed_workspace(db_session, user_id=user.id, slug="open-ws")
    resp = await _run(authed_client, ws, connected_agent, "SELECT * FROM analytics.leads")
    assert resp.status_code == 202


@pytest.mark.parametrize("sql", ["SELECT * FROM sales.leads", "SELECT * FROM SALES.leads"])
async def test_two_part_name_into_a_scoped_catalog_needs_a_grant(
    authed_client, mixed_ws, connected_agent, sql
):
    """DuckDB binds `sales.leads` to the `sales` catalog's `main` schema when
    `sales` is an attached catalog. Read only as schema `sales` of the open active
    catalog, it was let through without a grant on the scoped catalog."""
    ws, _open_cat, _scoped = mixed_ws
    resp = await _run(authed_client, ws, connected_agent, sql)
    assert resp.status_code == 403
    assert resp.json()["error"] == "grant_denied"
    assert "sales.main.leads" in str(resp.json())


async def test_two_part_name_into_a_scoped_catalog_allowed_with_its_grant(
    authed_client, mixed_ws, connected_agent, db_session, user
):
    ws, _open_cat, scoped = mixed_ws
    _grant(db_session, user, scoped, "reader", schema="main", table="leads")
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, "SELECT * FROM sales.leads")
    assert resp.status_code == 202


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pragma_table_info('test-cat.analytics.leads')",
        "SELECT * FROM iceberg_snapshots('test-cat.analytics.leads')",
        "SELECT * FROM snapshots()",
    ],
)
async def test_string_named_readers_denied_even_with_a_grant(
    authed_client, scoped_ws, connected_agent, db_session, user, sql
):
    """The string could name any table, so a grant on one does not admit them."""
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="leads")
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, sql)
    assert resp.status_code == 403
    assert resp.json()["error"] == "grant_denied"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM iceberg_snapshots('test-cat.analytics.leads')",
        "SELECT * FROM lake.snapshots()",
    ],
)
async def test_string_named_readers_still_work_in_an_open_workspace(
    authed_client, db_session, user, connected_agent, sql
):
    ws, _cat = await seed_workspace(db_session, user_id=user.id, slug="open-readers-ws")
    resp = await _run(authed_client, ws, connected_agent, sql)
    assert resp.status_code == 202


async def test_internal_sql_still_passes_the_scoped_check(scoped_ws, db_session, user):
    """What the platform itself dispatches must not trip the scoped refusals:
    editor autocomplete, maintenance apply, and the stats and sample probes."""
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="leads")
    await db_session.commit()
    internal = [
        sql_metadata._FUNCTIONS_SQL,
        sql_metadata._KEYWORDS_SQL,
        sql_metadata._TYPES_SQL,
        *(
            ";\n".join(
                render(kind, catalog=cat.slug, schema="analytics", table="leads", retention_days=7)
            )
            for kind in sorted(APPLICABLE_KINDS)
        ),
        "SELECT 1",
        f'SELECT * FROM "{cat.slug}"."analytics"."leads" LIMIT 100',
        f'DESCRIBE "{cat.slug}"."analytics"."leads"',
        f'SELECT * FROM (DESCRIBE "{cat.slug}"."analytics"."leads")',
    ]
    for sql in internal:
        await grant_service.assert_query_access(db_session, ws.id, user.id, sql, cat.slug, [cat])


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_parquet('s3://warehouse/test-cat/analytics/leads/data/x.parquet')",
        "SELECT * FROM 's3://warehouse/test-cat/analytics/leads/data/x.parquet'",
        "SELECT * FROM read_text('s3://warehouse/anything')",
        "SELECT * FROM iceberg_scan('s3://warehouse/test-cat/analytics/leads')",
        "SELECT * FROM \"glob\"('s3://warehouse/*')",
    ],
)
async def test_direct_file_reads_denied_in_a_scoped_workspace(
    authed_client, scoped_ws, connected_agent, db_session, user, sql
):
    """A data file read directly has no table reference to check a grant on."""
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="leads")
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, sql)
    assert resp.status_code == 403
    assert resp.json()["error"] == "grant_denied"


async def test_direct_file_reads_still_work_in_an_open_workspace(
    authed_client, db_session, user, connected_agent
):
    ws, _cat = await seed_workspace(db_session, user_id=user.id, slug="open-files-ws")
    resp = await _run(
        authed_client, ws, connected_agent, "SELECT * FROM read_parquet('s3://warehouse/x.parquet')"
    )
    assert resp.status_code == 202
