"""Acting for someone: a request made by one principal on behalf of another.

The assistant runs each turn with a short-lived token for its service account,
minted *on behalf of* the person who asked (``Credential.on_behalf_of_id``). A
request with such a token may do only what both of them may do: both must be
members of the workspace, the lower of their two roles applies, and in a scoped
catalog the lower of their two grant tiers. Without that the assistant would
reach whatever its account can, for anyone who may open the panel.

The authenticated user stays the service account: it is who ran each query and
whose name History shows. The person rides along on the request's database
session, bound once by :func:`api.deps.get_current_user`, and each access check
asks :func:`principals_for` who it must hold for. A check for any *other* user,
such as whether the target of a new grant is a member, is unaffected.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

_KEY = "duckhaven.delegation"


@dataclass(frozen=True)
class Delegation:
    """``actor_id`` authenticated, acting for ``delegator_id``."""

    actor_id: uuid.UUID
    delegator_id: uuid.UUID


def bind(db: AsyncSession, delegation: Delegation | None) -> None:
    """Make every access check on ``db`` hold for both principals, or clear that.

    Clearing matters as much as setting: a session that outlives one request,
    as a test's does, must not carry the previous caller's delegation into the
    next one.
    """
    if delegation is None:
        db.info.pop(_KEY, None)
    else:
        db.info[_KEY] = delegation


def bound(db: AsyncSession | Session) -> Delegation | None:
    """The delegation bound to ``db``, if any.

    Takes the sync ``Session`` too, which is what a mapper event is handed; both
    share the one ``info`` dict.
    """
    return db.info.get(_KEY)


def principals_for(db: AsyncSession, user_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
    """Everyone an access check for ``user_id`` must pass for.

    ``(user_id,)`` unless ``user_id`` is the actor of the delegation bound to
    ``db``; then ``(actor, delegator)``. Callers take the least of what each
    principal holds.
    """
    delegation = bound(db)
    if delegation is not None and delegation.actor_id == user_id:
        return (user_id, delegation.delegator_id)
    return (user_id,)


@contextlib.contextmanager
def acting_for(
    db: AsyncSession, actor_id: uuid.UUID | None, delegator_id: uuid.UUID | None
) -> Iterator[None]:
    """Bind a delegation for the checks inside the block, then restore the previous one.

    For work that is checked again outside the request that started it: a query
    parked while compute started, and replayed when an agent connects, carries
    its delegator on the row (``Query.on_behalf_of_id``) and is checked for both
    again. A no-op when there is no delegator.
    """
    if actor_id is None or delegator_id is None:
        yield
        return
    previous = bound(db)
    bind(db, Delegation(actor_id=actor_id, delegator_id=delegator_id))
    try:
        yield
    finally:
        bind(db, previous)
