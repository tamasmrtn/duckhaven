"""Issuing, listing and revoking personal access tokens.

Two routers own PATs -- `/admin/service-accounts/{id}/pats` for machine
identities and `/me/pats` for a person's own -- and they differ only in how the
owner is established. Keeping the mechanics here means a rule like the issuance
cap or the sort order is written once rather than drifting between them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import Credential
from api.services.auth import generate_pat, hash_token

#: Live tokens one principal may hold at once.
#:
#: Self-issuance needs no permission at all, so without a ceiling any signed-in
#: caller -- or anything that borrows their session for a moment -- can mint
#: credentials without bound, and they outlive the session that made them. A
#: person needs a handful: a laptop, a desktop, a tool or two. Twenty-five leaves
#: room to spare while keeping the collection genuinely bounded, which is also
#: what earns `/me/pats` its exemption from the pagination envelope.
MAX_LIVE_PATS = 25


class TooManyTokens(Exception):
    """The principal already holds `MAX_LIVE_PATS` tokens."""


def _owned(user_id: uuid.UUID):
    """Every PAT belonging to one principal.

    Filtering on `kind` as well as owner matters: session and agent credentials
    live in the same table, and a revoke keyed only on the id would reach them.
    """
    return (Credential.user_id == user_id, Credential.kind == "pat")


async def count_for(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(select(func.count()).select_from(Credential).where(*_owned(user_id)))
    return result.scalar_one()


async def issue(
    db: AsyncSession, user_id: uuid.UUID, *, expires_in_days: int | None
) -> tuple[Credential, str]:
    """Mint a token, returning the credential and the secret exactly once.

    Only the SHA-256 hash is stored, so the raw value returned here is the only
    copy that will ever exist.
    """
    if await count_for(db, user_id) >= MAX_LIVE_PATS:
        raise TooManyTokens
    token = generate_pat()
    expires_at = (
        None if expires_in_days is None else datetime.now(tz=UTC) + timedelta(days=expires_in_days)
    )
    cred = Credential(
        user_id=user_id,
        kind="pat",
        token=None,
        token_hash=hash_token(token),
        expires_at=expires_at,
    )
    db.add(cred)
    await db.commit()
    # Deliberately no `refresh`: the session keeps its in-memory values
    # (`expire_on_commit=False`), and reloading would replace the timezone-aware
    # `expires_at` we just computed with whatever the database hands back --
    # naive on SQLite, aware on Postgres, so the response shape would differ
    # between the test suite and production.
    return cred, token


async def list_for(db: AsyncSession, user_id: uuid.UUID) -> list[Credential]:
    """One principal's tokens, oldest first.

    The sort ends in the row id because `created_at` alone is not a total order:
    two tokens minted in the same clock tick would otherwise come back in an
    order the database was free to vary between calls.
    """
    result = await db.execute(
        select(Credential).where(*_owned(user_id)).order_by(Credential.created_at, Credential.id)
    )
    return list(result.scalars().all())


async def revoke(db: AsyncSession, user_id: uuid.UUID, pat_id: uuid.UUID) -> bool:
    """Delete one of a principal's tokens. False when they do not hold it."""
    result = await db.execute(select(Credential).where(Credential.id == pat_id, *_owned(user_id)))
    cred = result.scalar_one_or_none()
    if cred is None:
        return False
    await db.delete(cred)
    await db.commit()
    return True
