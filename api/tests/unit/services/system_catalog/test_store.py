"""The system catalog store when its database is missing or turned off.

What it creates, and that Postgres enforces the reader's limits, is proved
against a live server in tests/integration/test_system_catalog_store.py.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.config import settings
from api.models.user import Credential
from api.services.system_catalog import store


@pytest.fixture
async def unreachable(monkeypatch):
    """A store whose database refuses connections (nothing listens on port 1)."""
    monkeypatch.setattr(settings, "system_catalog_enabled", True)
    monkeypatch.setattr(
        settings,
        "system_catalog_database_url",
        "postgresql+asyncpg://duckhaven:x@127.0.0.1:1/duckhaven_system",
    )
    await store.dispose_engine()
    store.mark_stale()
    monkeypatch.setattr(store, "_unavailable_reason", None)
    yield
    await store.dispose_engine()


async def test_a_missing_database_leaves_the_store_unavailable_and_logs_once(
    unreachable, db_session, caplog
):
    factory = async_sessionmaker(db_session.bind)
    with caplog.at_level(logging.WARNING, logger="api.services.system_catalog.store"):
        assert await store.ensure(factory) is False
        assert await store.ensure(factory) is False

    assert not store.is_available()
    warnings = [r for r in caplog.records if r.name == "api.services.system_catalog.store"]
    assert len(warnings) == 1
    assert "scripts/enable-system-catalog.sh" in warnings[0].getMessage()
    assert "audit log and query history are unaffected" in warnings[0].getMessage()


async def test_turned_off_it_does_nothing(monkeypatch, db_session):
    monkeypatch.setattr(settings, "system_catalog_enabled", False)

    assert await store.ensure(async_sessionmaker(db_session.bind)) is False


async def test_the_reader_password_is_made_once_and_kept(db_session):
    first = await store.reader_password(db_session)
    second = await store.reader_password(db_session)

    assert first == second and len(first) >= 32
    rows = await db_session.scalars(
        select(Credential).where(Credential.kind == store.READER_CREDENTIAL_KIND)
    )
    assert len(rows.all()) == 1


async def test_the_reader_password_survives_a_factory_that_expires_on_commit(db_session):
    """Reading the new row after its commit would be a lazy load outside the greenlet."""
    async with async_sessionmaker(db_session.bind)() as db:
        password = await store.reader_password(db)

    assert password == await store.reader_password(db_session)
