"""Scoped-mode grant enforcement at SQL dispatch (issue #129)."""

from __future__ import annotations

import uuid

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


_DESCRIBED_PIVOT = "DESCRIBE PIVOT analytics.leads ON status USING count(*)"


async def test_described_pivot_still_needs_reader(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    """A PIVOT's output columns are named after values in the table, so the
    metadata tier must not describe one; `reader` can."""
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "metadata", table="leads")
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, _DESCRIBED_PIVOT)
    assert resp.status_code == 403


async def test_described_pivot_runs_with_reader(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="leads")
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, _DESCRIBED_PIVOT)
    assert resp.status_code == 202


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


# --- Names resolve where the statement runs, not where the session opened -----


async def _check(db_session, ws, user, cat, sql, *, catalog="__cat__", schema=None):
    await grant_service.assert_query_access(
        db_session,
        ws.id,
        user.id,
        sql,
        cat.slug if catalog == "__cat__" else catalog,
        [cat],
        active_schema=schema,
    )


async def test_a_use_moves_where_later_statements_are_checked(scoped_ws, db_session, user):
    """`USE` followed by an unqualified name reads the new schema, so that is the
    one the grant must cover."""
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader")  # the whole `analytics` schema
    await db_session.commit()
    await _check(db_session, ws, user, cat, "SELECT * FROM t")
    with pytest.raises(grant_service.GrantDenied, match=r"secret\.t"):
        await _check(db_session, ws, user, cat, f"USE {cat.slug}.secret; SELECT * FROM t")


async def test_a_use_target_is_not_read_as_a_table(scoped_ws, db_session, user):
    """`USE x` names where later names resolve; it reads nothing, so a schema
    grant elsewhere cannot be what lets it through."""
    ws, cat = scoped_ws
    await _check(db_session, ws, user, cat, f"USE {cat.slug}.secret")


@pytest.mark.parametrize(
    "prefix",
    ["USE secret", "SET schema = 'secret'", "SET search_path = 'test_cat.secret'"],
)
async def test_an_inexact_context_move_refuses_unqualified_names(
    scoped_ws, db_session, user, prefix
):
    """A one-part USE, SET schema and SET search_path leave where names resolve
    unknown to the check, so it refuses rather than guesses."""
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader")
    await db_session.commit()
    with pytest.raises(grant_service.GrantDenied, match="not known"):
        await _check(db_session, ws, user, cat, f"{prefix}; SELECT * FROM t")


async def test_an_unknown_context_still_allows_fully_qualified_names(scoped_ws, db_session, user):
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader")
    await db_session.commit()
    await _check(db_session, ws, user, cat, f"SELECT * FROM {cat.slug}.analytics.t", catalog=None)
    with pytest.raises(grant_service.GrantDenied, match="not known"):
        await _check(db_session, ws, user, cat, "SELECT * FROM t", catalog=None)


async def test_the_session_schema_is_where_unqualified_names_are_checked(
    scoped_ws, db_session, user
):
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader")  # `analytics` only
    await db_session.commit()
    with pytest.raises(grant_service.GrantDenied, match=r"secret\.t"):
        await _check(db_session, ws, user, cat, "SELECT * FROM t", schema="secret")


async def test_an_open_workspace_ignores_the_session_context(db_session, user):
    ws, cat = await seed_workspace(db_session, user_id=user.id, slug="open-ctx-ws")
    await _check(db_session, ws, user, cat, "SET search_path = 'x.y'; SELECT * FROM t")
    await _check(db_session, ws, user, cat, "SELECT * FROM t", catalog=None)


# --- Reading a result needs the grants running its query would ---------------


@pytest_asyncio.fixture
async def colleague(db_session):
    u = User(email="c@grants.local", password_hash=hash_password("pw"), name="C", role="user")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
def served_rows(monkeypatch):
    """Stand in for the agent fetch: the access check runs before it."""
    from api.schemas.query import RowsPageOut
    from api.services import query as query_service

    async def fake_page(db, query, *, limit, offset=0):
        return RowsPageOut(rows=[{"x": 1}], columns=["x"], cursor=None, total=1)

    monkeypatch.setattr(query_service, "fetch_result_page", fake_page)


async def _finished(db_session, ws, author, sql, agent=None, **fields):
    from api.models.query import Query

    q = Query(
        workspace_id=ws.id,
        agent_id=agent.id if agent is not None else None,
        user_id=author.id,
        sql=sql,
        status="done",
        result_path="/var/duckhaven-agent/results/x.parquet",
        row_count=1,
        profile={"summary": {"rows_returned": 1}},
        **fields,
    )
    db_session.add(q)
    await db_session.commit()
    await db_session.refresh(q)
    return q


async def test_a_colleagues_result_needs_a_grant_in_a_scoped_catalog(
    authed_client, scoped_ws, connected_agent, db_session, colleague, served_rows
):
    """Before, any member could read any result by id. Now the reader needs the
    grants the query's SQL needs, whoever ran it."""
    ws, cat = scoped_ws
    q = await _finished(
        db_session,
        ws,
        colleague,
        "SELECT * FROM analytics.leads",
        connected_agent,
        active_catalog=cat.slug,
    )
    rows = await authed_client.get(f"/queries/{q.id}/rows")
    assert rows.status_code == 403
    assert rows.json()["error"] == "grant_denied"
    assert (await authed_client.get(f"/queries/{q.id}/profile")).status_code == 403


async def test_a_grant_lets_the_result_be_read(
    authed_client, scoped_ws, connected_agent, db_session, user, colleague, served_rows
):
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="leads")
    await db_session.commit()
    q = await _finished(
        db_session,
        ws,
        colleague,
        "SELECT * FROM analytics.leads",
        connected_agent,
        active_catalog=cat.slug,
    )
    assert (await authed_client.get(f"/queries/{q.id}/rows")).status_code == 200
    assert (await authed_client.get(f"/queries/{q.id}/profile")).status_code == 200


async def test_the_author_is_rechecked_too(
    authed_client, scoped_ws, connected_agent, db_session, user, served_rows
):
    """A revoked grant also stops re-reading results run before the revocation."""
    ws, cat = scoped_ws
    q = await _finished(
        db_session,
        ws,
        user,
        "SELECT * FROM analytics.leads",
        connected_agent,
        active_catalog=cat.slug,
    )
    assert (await authed_client.get(f"/queries/{q.id}/rows")).status_code == 403


async def test_an_open_workspace_shares_results_with_every_member(
    authed_client, db_session, user, colleague, connected_agent, served_rows
):
    ws, cat = await seed_workspace(db_session, user_id=user.id, slug="open-read-ws")
    q = await _finished(db_session, ws, colleague, "SELECT * FROM analytics.leads", connected_agent)
    assert (await authed_client.get(f"/queries/{q.id}/rows")).status_code == 200


async def test_a_result_is_checked_in_the_catalog_it_ran_in(
    authed_client, mixed_ws, connected_agent, db_session, user, colleague, served_rows
):
    """The same unqualified SQL reads an open table from one catalog and a
    scoped one from another; the recorded catalog decides which is checked."""
    ws, open_cat, scoped_cat = mixed_ws
    in_open = await _finished(
        db_session,
        ws,
        colleague,
        "SELECT * FROM analytics.leads",
        connected_agent,
        active_catalog=open_cat.slug,
    )
    in_scoped = await _finished(
        db_session,
        ws,
        colleague,
        "SELECT * FROM analytics.leads",
        connected_agent,
        active_catalog=scoped_cat.slug,
    )
    assert (await authed_client.get(f"/queries/{in_open.id}/rows")).status_code == 200
    assert (await authed_client.get(f"/queries/{in_scoped.id}/rows")).status_code == 403


async def test_a_session_result_is_checked_in_the_schema_it_ran_in(
    authed_client, scoped_ws, connected_agent, db_session, user, colleague, served_rows
):
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader")  # `analytics` only
    await db_session.commit()
    common = {"origin": "session", "active_catalog": cat.slug}
    here = await _finished(
        db_session,
        ws,
        colleague,
        "SELECT * FROM leads",
        connected_agent,
        active_schema="analytics",
        **common,
    )
    moved = await _finished(
        db_session,
        ws,
        colleague,
        "SELECT * FROM leads",
        connected_agent,
        active_schema="secret",
        **common,
    )
    assert (await authed_client.get(f"/queries/{here.id}/rows")).status_code == 200
    assert (await authed_client.get(f"/queries/{moved.id}/rows")).status_code == 403


async def test_a_session_result_from_an_unknown_context_needs_qualified_names(
    authed_client, scoped_ws, connected_agent, db_session, user, served_rows
):
    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader")
    await db_session.commit()
    unqualified = await _finished(
        db_session, ws, user, "SELECT * FROM leads", connected_agent, origin="session"
    )
    qualified = await _finished(
        db_session,
        ws,
        user,
        f"SELECT * FROM {cat.slug}.analytics.leads",
        connected_agent,
        origin="session",
    )
    assert (await authed_client.get(f"/queries/{unqualified.id}/rows")).status_code == 403
    assert (await authed_client.get(f"/queries/{qualified.id}/rows")).status_code == 200


async def test_dispatch_records_the_catalog_names_resolved_in(
    authed_client, scoped_ws, connected_agent, db_session, user
):
    from api.models.query import Query

    ws, cat = scoped_ws
    _grant(db_session, user, cat, "reader", table="leads")
    await db_session.commit()
    resp = await _run(authed_client, ws, connected_agent, "SELECT * FROM analytics.leads")
    assert resp.status_code == 202
    row = await db_session.get(Query, uuid.UUID(resp.json()["id"]))
    await db_session.refresh(row)
    assert row.active_catalog == cat.slug


async def test_scoped_catalog_ids_agrees_with_is_scoped(db_session, mixed_ws):
    """One query for every attached catalog, with the same answer as asking each:
    an open attachment and a catalog the workspace does not attach are not scoped."""
    ws, open_cat, scoped_cat = mixed_ws
    unattached = Catalog(
        slug="elsewhere",
        name="Elsewhere",
        polaris_name="mixed-ws-elsewhere",
        storage_backend_id=scoped_cat.storage_backend_id,
        created_by=scoped_cat.created_by,
    )
    db_session.add(unattached)
    await db_session.commit()
    catalogs = [open_cat, scoped_cat, unattached]

    batched = await grant_service.scoped_catalog_ids(db_session, ws.id, catalogs)

    one_by_one = {c.id for c in catalogs if await grant_service.is_scoped(db_session, ws.id, c)}
    assert batched == one_by_one == {scoped_cat.id}
    assert await grant_service.scoped_catalog_ids(db_session, ws.id, []) == set()
