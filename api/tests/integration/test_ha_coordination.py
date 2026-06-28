"""HA control-plane coordination against real Postgres.

Covers the two pieces that only work with a real database: cluster-wide agent
presence backed by ``Agent.owner_*`` columns, and the scanner's advisory-lock
leader election. Both are env-gated via the ``DATABASE_URL`` Postgres fixtures.
"""

from __future__ import annotations

import asyncio
import random

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.models.agent import Agent
from api.models.user import Credential
from api.services.agent_dispatch import (
    claim_agent_owner,
    connected_agent_ids,
    is_agent_connected,
    release_agent_owner,
)
from api.services.bootstrap import seed_agent_bootstrap_token
from api.services.maintenance import scanner as scanner_mod
from api.services.maintenance.scanner import scan_leadership

pytestmark = pytest.mark.integration


async def test_presence_roundtrip_on_real_postgres(db_session):
    agent = Agent(name="ha-agent", status="unavailable")
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    assert not await is_agent_connected(db_session, agent.id)

    await claim_agent_owner(db_session, agent.id)
    assert str(agent.id) in await connected_agent_ids(db_session)
    assert await is_agent_connected(db_session, agent.id)

    await release_agent_owner(db_session, agent.id)
    assert not await is_agent_connected(db_session, agent.id)
    assert str(agent.id) not in await connected_agent_ids(db_session)


@pytest_asyncio.fixture
def _unique_lock_key(monkeypatch):
    """Advisory locks share a database-global namespace, so randomize the key per
    test to stay isolated from parallel workers and other suites."""
    monkeypatch.setattr(scanner_mod, "_SCANNER_LOCK_KEY", random.randint(1, 2**31 - 1))


async def test_scan_leadership_is_mutually_exclusive(pg_engine, _unique_lock_key):
    """Only one holder of the advisory lock at a time; it frees on exit."""
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with scan_leadership(factory) as leader:
        assert leader is True
        async with scan_leadership(factory) as contender:
            assert contender is False
    # Lock released after the first holder exits.
    async with scan_leadership(factory) as leader_again:
        assert leader_again is True


async def test_concurrent_bootstrap_seed_is_race_safe(db_session):
    """Multiple replicas seeding the bootstrap token at once must not crash and
    must leave exactly one credential row. ``db_session`` creates the schema's
    tables; the concurrent seeds run on their own sessions over the same bind."""
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)

    async def seed() -> None:
        async with factory() as db:
            await seed_agent_bootstrap_token(db, "dh_boot_race", ttl_hours=24)

    await asyncio.gather(*(seed() for _ in range(5)))

    async with factory() as db:
        count = await db.scalar(
            select(func.count()).select_from(Credential).where(Credential.token == "dh_boot_race")
        )
    assert count == 1
