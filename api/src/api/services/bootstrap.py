"""Principals and credentials the API seeds for itself on startup.

Folds in the former agent-bootstrap compose one-shot: the API owns the
`credentials` table (and applies its migrations), so it seeds the token itself
once migrations have run. Idempotent — re-seeds only when the token is absent
(the agent consumes it on first registration and reconnects with a session
token thereafter), mirroring the one-shot's `WHERE NOT EXISTS`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.models.user import SERVICE_ACCOUNT_PROVIDER, Credential, User
from api.services.assistant.identity import ASSISTANT_EMAIL, ASSISTANT_NAME


async def seed_agent_bootstrap_token(db: AsyncSession, token: str | None, ttl_hours: int) -> None:
    """Insert an `agent_bootstrap` credential for `token` if none exists.

    The absence check is a fast path, not a guard: with multiple API replicas
    booting concurrently, two replicas can both see no row and both try to
    INSERT. The commit is therefore wrapped so the unique-key collision from
    the losing replica is swallowed (the token already exists, which is all we
    wanted) instead of crashing that replica's startup.
    """
    if not token:
        return

    existing = await db.execute(select(Credential.id).where(Credential.token == token))
    if existing.scalar_one_or_none() is not None:
        return

    db.add(
        Credential(
            kind="agent_bootstrap",
            token=token,
            expires_at=datetime.now(tz=UTC) + timedelta(hours=ttl_hours),
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        # Another replica inserted the bootstrap token between our check and
        # commit. The token is present, which is the desired end state.
        await db.rollback()


async def ensure_assistant_service_account(db: AsyncSession) -> None:
    """Create the service account the assistant acts as, if it isn't there yet.

    Turning the assistant on used to mean creating this account by hand and naming
    it in config; the account is fixed now, so startup can make it. It lands with
    the zero-permission ``user`` role and no workspace membership — the same place
    a hand-made service account starts — so enabling the assistant grants no data
    access on its own.

    An existing row is left exactly as it is, **including an inactive one**:
    disabling the account is a deliberate kill switch, and startup must not undo
    it. Deleting it in the admin UI while the assistant is enabled does get it
    back on the next restart, which is the point of seeding it here.

    Like ``seed_agent_bootstrap_token``, the absence check is a fast path rather
    than a guard: concurrent replicas can both see no row and both insert, so the
    loser's unique-key collision is swallowed.
    """
    if not settings.assistant_enabled:
        return

    existing = await db.execute(select(User.id).where(User.email == ASSISTANT_EMAIL))
    if existing.scalar_one_or_none() is not None:
        return

    db.add(
        User(
            email=ASSISTANT_EMAIL,
            name=ASSISTANT_NAME,
            password_hash=None,
            role="user",
            auth_provider=SERVICE_ACCOUNT_PROVIDER,
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        # Another replica created it between our check and commit. The account is
        # present, which is the desired end state.
        await db.rollback()
