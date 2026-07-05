import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.config import settings
from api.models.user import Credential, User
from api.services.assistant.identity import (
    AssistantIdentityError,
    ephemeral_pat,
    resolve_service_account,
)
from api.services.auth import get_pat_user


@pytest_asyncio.fixture
async def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def service_account(db_session):
    account = User(
        email="assistant@service-account.local",
        name="Assistant",
        role="user",
        auth_provider="service_account",
    )
    db_session.add(account)
    await db_session.commit()
    await db_session.refresh(account)
    return account


async def test_resolve_requires_configured_slug(db_session, monkeypatch):
    monkeypatch.setattr(settings, "assistant_service_account_slug", None)
    with pytest.raises(AssistantIdentityError):
        await resolve_service_account(db_session)


async def test_resolve_finds_service_account(db_session, service_account, monkeypatch):
    monkeypatch.setattr(settings, "assistant_service_account_slug", "assistant")
    resolved = await resolve_service_account(db_session)
    assert resolved.id == service_account.id


async def test_ephemeral_pat_minted_usable_then_deleted(factory, service_account):
    async with ephemeral_pat(factory, service_account.id) as token:
        assert token.startswith("dh_pat_")
        async with factory() as db:
            user = await get_pat_user(db, token)
            assert user is not None
            assert user.id == service_account.id
    # After the context exits, the credential is gone.
    async with factory() as db:
        count = (await db.execute(select(func.count()).select_from(Credential))).scalar_one()
        assert count == 0
