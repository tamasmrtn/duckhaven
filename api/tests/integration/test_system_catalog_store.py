"""The system catalog store: its tables, and a reader login that reaches nothing else.

`tests/deploy/test_compose_system_catalog.py` asserts the init script's REVOKEs
are written; this asserts Postgres enforces them, together with the grants
`ensure()` gives the reader.
"""

from __future__ import annotations

import os
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


async def _reader(session_factory, url: str | None = None) -> asyncpg.Connection:
    async with session_factory() as db:
        password = await store.reader_password(db)
    return await asyncpg.connect(
        _dsn(url or settings.system_catalog_database_url, user=store.READER_ROLE, password=password)
    )


async def test_ensure_creates_the_tables_and_a_reader_that_reads_them(system_store):
    assert await store.ensure(system_store) is True
    assert store.is_available()

    reader = await _reader(system_store)
    try:
        for table in ("access.audit", "access.grants", "query.history"):
            assert await reader.fetchval(f"SELECT count(*) FROM {table}") == 0
    finally:
        await reader.close()


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
    reader = await _reader(system_store)
    try:
        assert await reader.fetchval("SELECT count(*) FROM query.history") == 0
    finally:
        await reader.close()
