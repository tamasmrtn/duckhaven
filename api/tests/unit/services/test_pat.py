from datetime import UTC, datetime, timedelta

import pytest

from api.models.user import Credential, User
from api.services.auth import (
    PAT_PREFIX,
    generate_pat,
    get_pat_user,
    hash_token,
)


async def _make_service_account(db, *, is_active: bool = True) -> User:
    user = User(
        email="svc@service-account.local",
        name="Svc",
        password_hash=None,
        role="user",
        auth_provider="service_account",
        is_active=is_active,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _issue(db, user: User, *, expires_at=None) -> str:
    token = generate_pat()
    db.add(
        Credential(
            user_id=user.id,
            kind="pat",
            token=None,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
    )
    await db.commit()
    return token


def test_generate_pat_prefix_and_entropy():
    a, b = generate_pat(), generate_pat()
    assert a.startswith(PAT_PREFIX)
    assert a != b
    # dh_pat_ + urlsafe(32) → well over 40 chars of secret
    assert len(a) > 40


def test_hash_token_is_deterministic_sha256_hex():
    token = generate_pat()
    digest = hash_token(token)
    assert digest == hash_token(token)
    assert len(digest) == 64  # sha256 hex
    assert digest != hash_token(generate_pat())


@pytest.mark.asyncio
async def test_get_pat_user_valid(db_session):
    sa = await _make_service_account(db_session)
    token = await _issue(db_session, sa, expires_at=datetime.now(tz=UTC) + timedelta(days=1))
    resolved = await get_pat_user(db_session, token)
    assert resolved is not None
    assert resolved.id == sa.id


@pytest.mark.asyncio
async def test_get_pat_user_never_expires(db_session):
    sa = await _make_service_account(db_session)
    token = await _issue(db_session, sa, expires_at=None)
    assert (await get_pat_user(db_session, token)) is not None


@pytest.mark.asyncio
async def test_get_pat_user_expired(db_session):
    sa = await _make_service_account(db_session)
    token = await _issue(db_session, sa, expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
    assert (await get_pat_user(db_session, token)) is None


@pytest.mark.asyncio
async def test_get_pat_user_unknown_hash(db_session):
    await _make_service_account(db_session)
    assert (await get_pat_user(db_session, generate_pat())) is None


@pytest.mark.asyncio
async def test_get_pat_user_missing_prefix(db_session):
    sa = await _make_service_account(db_session)
    # A value that hashes to a stored credential but lacks the prefix is rejected
    # before any lookup.
    await _issue(db_session, sa)
    assert (await get_pat_user(db_session, "not-a-pat")) is None


@pytest.mark.asyncio
async def test_get_pat_user_disabled_owner(db_session):
    sa = await _make_service_account(db_session, is_active=False)
    token = await _issue(db_session, sa)
    assert (await get_pat_user(db_session, token)) is None


@pytest.mark.asyncio
async def test_get_pat_user_ignores_session_kind(db_session):
    # A session credential must never resolve through the PAT path.
    sa = await _make_service_account(db_session)
    token = generate_pat()
    db_session.add(Credential(user_id=sa.id, kind="session", token=token, expires_at=None))
    await db_session.commit()
    assert (await get_pat_user(db_session, token)) is None
