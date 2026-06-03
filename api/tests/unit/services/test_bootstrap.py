"""Tests for the agent bootstrap-token seeding done on API startup."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import func, select

from api.models.user import Credential
from api.services.bootstrap import seed_agent_bootstrap_token


async def _tokens(db) -> list[Credential]:
    result = await db.execute(select(Credential))
    return list(result.scalars().all())


@pytest.mark.asyncio
async def test_seeds_bootstrap_credential(db_session) -> None:
    await seed_agent_bootstrap_token(db_session, "tok-123", ttl_hours=240)

    creds = await _tokens(db_session)
    assert len(creds) == 1
    cred = creds[0]
    assert cred.kind == "agent_bootstrap"
    assert cred.token == "tok-123"
    # SQLite returns naive datetimes; compare tz-agnostically.
    assert cred.expires_at is not None
    assert cred.expires_at.replace(tzinfo=None) > datetime.now().replace(tzinfo=None)


@pytest.mark.asyncio
async def test_second_run_is_idempotent(db_session) -> None:
    await seed_agent_bootstrap_token(db_session, "tok-123", ttl_hours=240)
    await seed_agent_bootstrap_token(db_session, "tok-123", ttl_hours=240)

    count = await db_session.scalar(select(func.count()).select_from(Credential))
    assert count == 1


@pytest.mark.asyncio
async def test_no_token_seeds_nothing(db_session) -> None:
    await seed_agent_bootstrap_token(db_session, None, ttl_hours=240)
    await seed_agent_bootstrap_token(db_session, "", ttl_hours=240)

    assert await _tokens(db_session) == []
