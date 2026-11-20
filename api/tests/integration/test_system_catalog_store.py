"""The system catalog store: its tables, and a reader login that reaches nothing else.

`tests/deploy/test_compose_system_catalog.py` asserts the init script's REVOKEs
are written; this asserts Postgres enforces them, together with the grants
`ensure()` gives the reader.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
import sqlalchemy as sa

from api.config import settings
from api.models.user import Credential
from api.services.system_catalog import store

pytestmark = pytest.mark.integration


def _dsn(url: str, *, user: str | None = None, password: str | None = None) -> str:
    """A libpq DSN for `url`, optionally as another login."""
    parts = urlsplit(url)
    netloc = parts.netloc
    if user is not None:
        host = parts.hostname or "localhost"
        port = f":{parts.port}" if parts.port else ""
        netloc = f"{user}:{password}@{host}{port}"
    return urlunsplit(("postgresql", netloc, parts.path, "", ""))


async def _reader(
    session_factory, url: str | None = None, *, readable: str | None = None
) -> asyncpg.Connection:
    """The reader login, connected as an agent does: with the row filter's settings
    when ``readable`` is given, and with none otherwise."""
    async with session_factory() as db:
        password = await store.reader_password(db)
    server_settings = (
        {
            "duckhaven.workspace": str(uuid.uuid4()),
            "duckhaven.principals": str(uuid.uuid4()),
            "duckhaven.readable": readable,
        }
        if readable is not None
        else None
    )
    return await asyncpg.connect(
        _dsn(
            url or settings.system_catalog_database_url, user=store.READER_ROLE, password=password
        ),
        server_settings=server_settings,
    )


async def _one_row_each() -> None:
    """An event in access.audit and a run in query.history, written as the owner."""
    now = datetime.now(UTC)
    async with store.get_engine().begin() as conn:
        await conn.execute(
            sa.insert(store.AUDIT).values(
                id=uuid.uuid4(),
                event_time=now,
                event_version=1,
                category="access",
                action="catalog_grant.create",
                outcome="success",
                actor_type="user",
                self_change=False,
            )
        )
        await conn.execute(
            sa.insert(store.HISTORY).values(
                id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
                status="done",
                sql="SELECT 1",
                started_at=now,
                finished_at=now,
            )
        )


async def test_ensure_creates_the_tables_and_a_reader_that_reads_them(system_store):
    assert await store.ensure(system_store) is True
    assert store.is_available()

    reader = await _reader(system_store, readable=",".join(t.qualified for t in store.TABLES))
    try:
        for table in store.TABLES:
            assert await reader.fetchval(f"SELECT count(*) FROM {table.qualified}") == 0
    finally:
        await reader.close()


async def test_the_reader_reads_only_the_tables_its_settings_name(system_store):
    await store.ensure(system_store)
    await _one_row_each()

    async def counts(readable: str | None) -> tuple[int, int]:
        reader = await _reader(system_store, readable=readable)
        try:
            return (
                await reader.fetchval("SELECT count(*) FROM access.audit"),
                await reader.fetchval("SELECT count(*) FROM query.history"),
            )
        finally:
            await reader.close()

    assert await counts("access.audit,query.history") == (1, 1)
    assert await counts("access.audit") == (1, 0)
    assert await counts("") == (0, 0)
    # An agent that attached without them, as one too old to send them would.
    assert await counts(None) == (0, 0)


async def test_ensure_adds_a_column_an_existing_store_lacks(system_store):
    """The tables only ever gain columns; an upgraded deployment gets them."""
    await store.ensure(system_store)
    async with store.get_engine().begin() as conn:
        await conn.execute(sa.text("ALTER TABLE query.history DROP COLUMN tags"))
        await conn.execute(sa.text("DROP INDEX query.history_user_idx"))
    store.mark_stale()

    assert await store.ensure(system_store) is True

    async with store.get_engine().connect() as conn:
        columns = set(
            await conn.scalars(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'query' AND table_name = 'history'"
                )
            )
        )
        indexes = set(
            await conn.scalars(
                sa.text("SELECT indexname FROM pg_indexes WHERE schemaname = 'query'")
            )
        )
    assert "tags" in columns
    assert "history_user_idx" in indexes


async def test_the_reader_cannot_write_even_with_read_only_turned_off(system_store):
    await store.ensure(system_store)
    reader = await _reader(system_store)
    try:
        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await reader.execute("DELETE FROM access.audit")
        # Read-only is only a session default; the grants are what hold.
        await reader.execute("SET default_transaction_read_only = off")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reader.execute("DELETE FROM access.audit")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reader.execute("CREATE TABLE public.leak (x int)")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reader.execute("CREATE TABLE access.leak (x int)")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reader.execute("CREATE TEMP TABLE leak (x int)")
    finally:
        await reader.close()


async def test_the_reader_cannot_see_where_the_copy_has_got_to(system_store):
    await store.ensure(system_store)
    reader = await _reader(system_store)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reader.fetchval("SELECT count(*) FROM _state.cursors")
    finally:
        await reader.close()


async def test_the_reader_cannot_connect_to_the_control_plane(system_store):
    """It holds users, password hashes and session tokens."""
    await store.ensure(system_store)
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="CONNECT"):
        await _reader(system_store, os.environ["DATABASE_URL"])


async def test_the_reader_login_is_limited(system_store):
    settings.system_catalog_reader_connection_limit = 7
    try:
        await store.ensure(system_store)
    finally:
        settings.system_catalog_reader_connection_limit = 20
    async with store.get_engine().connect() as conn:
        role = (
            await conn.execute(
                sa.text(
                    "SELECT rolsuper, rolcreaterole, rolcreatedb, rolconnlimit, rolconfig "
                    "FROM pg_roles WHERE rolname = :r"
                ),
                {"r": store.READER_ROLE},
            )
        ).one()
    assert (role.rolsuper, role.rolcreaterole, role.rolcreatedb) == (False, False, False)
    assert role.rolconnlimit == 7
    assert set(role.rolconfig) >= {
        "default_transaction_read_only=on",
        "statement_timeout=5min",
        "idle_session_timeout=10min",
    }


async def test_ensure_is_idempotent_and_keeps_one_password(system_store):
    assert await store.ensure(system_store) is True
    store.mark_stale()
    assert await store.ensure(system_store) is True

    async with system_store() as db:
        kinds = await db.scalars(
            sa.select(Credential.token).where(Credential.kind == store.READER_CREDENTIAL_KIND)
        )
        assert len(kinds.all()) == 1
    reader = await _reader(system_store)
    await reader.close()


async def test_ensure_repairs_a_dropped_table(system_store):
    await store.ensure(system_store)
    async with store.get_engine().begin() as conn:
        await conn.execute(sa.text("DROP TABLE query.history"))

    assert await store.ensure(system_store) is True
    reader = await _reader(system_store, readable="query.history")
    try:
        assert await reader.fetchval("SELECT count(*) FROM query.history") == 0
    finally:
        await reader.close()
