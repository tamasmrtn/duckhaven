"""Identity for the assistant: the bound service account and its ephemeral PAT.

The assistant acts as an ordinary service-account principal. Because PATs are
stored only as a SHA-256 digest (unreconstructable) and there is no secret-at-rest
mechanism, the runtime **mints a short-lived PAT per turn** for the bound account,
uses it for that turn's loopback calls, and deletes it afterwards. This reuses the
exact audited PAT path (``generate_pat``/``hash_token``/``get_pat_user``) with no
crypto and no recoverable secret persisted.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.models.user import Credential, User
from api.services.auth import generate_pat, hash_token

SERVICE_ACCOUNT_PROVIDER = "service_account"


class AssistantIdentityError(RuntimeError):
    """The assistant's service account is unconfigured, missing, or disabled."""


def _service_account_email(slug: str) -> str:
    return f"{slug}@service-account.local"


async def resolve_service_account(db: AsyncSession) -> User:
    """Return the configured service-account ``User`` the assistant acts as.

    Raises :class:`AssistantIdentityError` when the slug is unset, no matching
    active service account exists, or the account is not a service account.
    """
    slug = settings.assistant_service_account_slug
    if not slug:
        raise AssistantIdentityError(
            "assistant_service_account_slug is not configured; create a service "
            "account and set ASSISTANT_SERVICE_ACCOUNT_SLUG."
        )
    result = await db.execute(select(User).where(User.email == _service_account_email(slug)))
    user = result.scalar_one_or_none()
    if user is None:
        raise AssistantIdentityError(f"No service account found for slug '{slug}'.")
    if user.auth_provider != SERVICE_ACCOUNT_PROVIDER:
        raise AssistantIdentityError(f"Principal '{slug}' is not a service account.")
    if not user.is_active:
        raise AssistantIdentityError(f"Service account '{slug}' is disabled.")
    return user


@contextlib.asynccontextmanager
async def ephemeral_pat(
    session_factory: async_sessionmaker[AsyncSession], service_account_id
) -> AsyncIterator[str]:
    """Mint a short-lived PAT for the service account, yield the raw token, delete it.

    Uses its own committed session so the loopback (which resolves the PAT in a
    separate session via ``get_pat_user``) can see it. A crash before cleanup leaves
    a row that expires within ``assistant_pat_ttl_s`` and is ignored by the expiry
    filter in ``get_pat_user``.
    """
    token = generate_pat()
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=settings.assistant_pat_ttl_s)
    async with session_factory() as db:
        cred = Credential(
            user_id=service_account_id,
            kind="pat",
            token=None,
            token_hash=hash_token(token),
            expires_at=expires_at,
        )
        db.add(cred)
        await db.commit()
        cred_id = cred.id
    try:
        yield token
    finally:
        async with session_factory() as db:
            await db.execute(delete(Credential).where(Credential.id == cred_id))
            await db.commit()
