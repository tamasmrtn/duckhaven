"""Seed the single-use agent bootstrap credential on API startup.

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

from api.models.user import Credential


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
