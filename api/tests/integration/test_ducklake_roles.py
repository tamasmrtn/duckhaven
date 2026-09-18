"""The DuckLake agent role reaches its own database and nothing else.

`tests/deploy/test_compose_ducklake.py` asserts the REVOKEs are written; this
asserts Postgres enforces them. See
deploy/postgres-init/20-create-ducklake-db.sh.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

AGENT_USER = os.getenv("DUCKLAKE_AGENT_USER", "ducklake_agent")
AGENT_PASSWORD = os.getenv("DUCKLAKE_AGENT_PASSWORD", "")


def _agent_url(database: str) -> str:
    """The admin DATABASE_URL with the agent's credentials and a target db."""
    raw = os.getenv("DATABASE_URL")
    if not raw:
        pytest.skip("DATABASE_URL not set; skipping DuckLake role test")
    if not AGENT_PASSWORD:
        pytest.skip("DUCKLAKE_AGENT_PASSWORD not set; skipping DuckLake role test")
    parts = urlsplit(raw)
    host = parts.hostname or "localhost"
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{AGENT_USER}:{AGENT_PASSWORD}@{host}{port}"
    return urlunsplit(("postgresql+asyncpg", netloc, f"/{database}", "", ""))


async def _connect(database: str) -> str:
    engine = create_async_engine(_agent_url(database), poolclass=None)
    try:
        async with engine.connect() as conn:
            row = await conn.execute(text("SELECT current_database()"))
            return str(row.scalar_one())
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_agent_role_can_open_the_ducklake_database() -> None:
    assert await _connect("ducklake") == "ducklake"


@pytest.mark.asyncio
@pytest.mark.parametrize("database", ["duckhaven", "polaris"])
async def test_agent_role_cannot_open_the_control_plane_databases(database: str) -> None:
    """If this ever passes, an agent can read the credentials table.

    Refused at connect time: asyncpg raises before SQLAlchemy can wrap it, so
    catch both.
    """
    with pytest.raises((asyncpg.PostgresError, DBAPIError)) as excinfo:
        await _connect(database)
    assert "permission denied" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_agent_role_is_not_a_superuser() -> None:
    """A superuser bypasses every CONNECT grant above."""
    engine = create_async_engine(_agent_url("ducklake"), poolclass=None)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = :n"
                ),
                {"n": AGENT_USER},
            )
            assert result.one() == (False, False, False)
    finally:
        await engine.dispose()
