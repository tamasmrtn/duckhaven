"""Just-in-time provisioning for federated (OIDC/LDAP) logins.

A successful federation maps the IdP's group claims to a DuckHaven global role
and creates or updates the matching local ``User`` row. DuckHaven stays the sole
permission authority (D10): the IdP supplies identity and group membership; the
role is resolved here and enforced at the API boundary.
"""

from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.user import User
from api.services.auth import get_user_by_email


def resolve_role(groups: Iterable[str], role_map: dict[str, str]) -> str:
    """Map IdP groups to a global role. ``admin`` wins when present; otherwise the
    first matched role; otherwise the default ``user``."""
    matched = {role_map[g] for g in groups if g in role_map}
    if "admin" in matched:
        return "admin"
    if matched:
        return sorted(matched)[0]
    return "user"


async def provision_federated_user(
    db: AsyncSession,
    *,
    email: str,
    name: str,
    subject: str,
    provider: str,
    role: str,
) -> User:
    """Create or update a federated user, then return it.

    Re-syncs ``name`` and ``role`` from the IdP on every login (the directory is
    authoritative). Refuses to bind to an account that authenticates through a
    different provider — this is what stops a federated identity from hijacking
    the local break-glass admin (or vice versa).
    """
    user = await get_user_by_email(db, email)
    if user is not None:
        if user.auth_provider != provider:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Email already registered with the {user.auth_provider} provider.",
            )
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled.")
        user.name = name
        user.role = role
        user.external_subject = subject
        await db.commit()
        await db.refresh(user)
        return user

    user = User(
        email=email,
        name=name,
        password_hash=None,
        role=role,
        auth_provider=provider,
        external_subject=subject,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
