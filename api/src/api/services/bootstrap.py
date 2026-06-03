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
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import Credential


async def seed_agent_bootstrap_token(db: AsyncSession, token: str | None, ttl_hours: int) -> None:
    """Insert an `agent_bootstrap` credential for `token` if none exists."""
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
    await db.commit()
