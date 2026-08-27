"""Tests for the principals and credentials seeded on API startup."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import func, select

from api.config import settings
from api.models.user import Credential, User
from api.models.workspace import WorkspaceMember
from api.services.assistant.identity import ASSISTANT_EMAIL
from api.services.bootstrap import ensure_assistant_service_account, seed_agent_bootstrap_token


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


@pytest.mark.asyncio
async def test_concurrent_insert_is_swallowed(db_session, monkeypatch) -> None:
    """Two replicas booting at once can both pass the absence check and both
    INSERT; the loser's unique-key collision must be swallowed, not crash
    startup. Forcing the commit to raise IntegrityError exercises that path."""
    from sqlalchemy.exc import IntegrityError

    async def _raise() -> None:
        raise IntegrityError("duplicate", None, Exception("duplicate"))

    rolled_back = False
    orig_rollback = db_session.rollback

    async def _rollback() -> None:
        nonlocal rolled_back
        rolled_back = True
        await orig_rollback()

    monkeypatch.setattr(db_session, "commit", _raise)
    monkeypatch.setattr(db_session, "rollback", _rollback)

    # Must not raise — the token already existing is the desired end state.
    await seed_agent_bootstrap_token(db_session, "race-tok", ttl_hours=240)
    assert rolled_back is True


async def _assistant_account(db) -> User | None:
    result = await db.execute(select(User).where(User.email == ASSISTANT_EMAIL))
    return result.scalar_one_or_none()


@pytest.fixture
def assistant_on(monkeypatch):
    monkeypatch.setattr(settings, "assistant_enabled", True)


@pytest.mark.asyncio
async def test_creates_the_assistant_service_account(db_session, assistant_on) -> None:
    await ensure_assistant_service_account(db_session)

    account = await _assistant_account(db_session)
    assert account is not None
    assert account.auth_provider == "service_account"
    assert account.password_hash is None
    # Zero-permission and unattached: enabling the assistant grants no data access
    # on its own, it only creates the principal an admin then grants.
    assert account.role == "user"
    memberships = (
        await db_session.execute(
            select(func.count())
            .select_from(WorkspaceMember)
            .where(WorkspaceMember.user_id == account.id)
        )
    ).scalar_one()
    assert memberships == 0


@pytest.mark.asyncio
async def test_does_not_create_the_account_when_the_assistant_is_off(
    db_session, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "assistant_enabled", False)
    await ensure_assistant_service_account(db_session)

    assert await _assistant_account(db_session) is None


@pytest.mark.asyncio
async def test_creating_the_account_is_idempotent(db_session, assistant_on) -> None:
    await ensure_assistant_service_account(db_session)
    await ensure_assistant_service_account(db_session)

    count = (
        await db_session.execute(
            select(func.count()).select_from(User).where(User.email == ASSISTANT_EMAIL)
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_leaves_a_disabled_account_disabled(db_session, assistant_on) -> None:
    # Disabling the account is a deliberate kill switch; a restart must not undo it.
    await ensure_assistant_service_account(db_session)
    account = await _assistant_account(db_session)
    account.is_active = False
    await db_session.commit()

    await ensure_assistant_service_account(db_session)

    account = await _assistant_account(db_session)
    assert account.is_active is False
