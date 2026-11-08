"""Just-in-time provisioning for federated (OIDC/LDAP) logins.

A successful federation maps the IdP's group claims to a DuckHaven global role
and creates or updates the matching local ``User`` row. DuckHaven stays the sole
permission authority (D10): the IdP supplies identity and group membership; the
role is resolved here and enforced at the API boundary.
"""

from collections.abc import Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
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


class ProvisioningRefused(HTTPException):
    """A federated sign-in that may not bind to, or create, an account."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


async def _by_subject(db: AsyncSession, provider: str, subject: str) -> User | None:
    result = await db.execute(
        select(User).where(User.auth_provider == provider, User.external_subject == subject)
    )
    return result.scalar_one_or_none()


async def provision_federated_user(
    db: AsyncSession,
    *,
    email: str,
    name: str,
    subject: str,
    provider: str,
    role: str,
    email_verified: bool,
    legacy_provider: str | None = None,
    rebind_on_verified_email: bool = False,
) -> User:
    """Create or update a federated user, then return it.

    An account is the identity one provider vouches for, so the sign-in resolves,
    in order, to:

    1. the account this ``provider`` already bound to ``subject``;
    2. an account bound to ``subject`` under ``legacy_provider``, the name a
       provider's accounts carried before each provider had its own, with the same
       email — it is re-labelled to ``provider``;
    3. an account with this email, only when the provider attests the email and
       the account is this provider's and bound to no subject yet, or, with
       ``rebind_on_verified_email``, bound to another of its subjects (a directory
       entry that moved);
    4. a new account, when no account has this email.

    Any other account with this email is refused with ``ProvisioningRefused``.
    That is what stops one provider's sign-in from taking over another's account,
    or the local break-glass admin. ``name`` and ``role`` re-sync from the
    provider on every sign-in: the directory is authoritative.
    """
    user = await _by_subject(db, provider, subject)
    if user is None and legacy_provider is not None:
        legacy = await _by_subject(db, legacy_provider, subject)
        if legacy is not None and legacy.email == email:
            legacy.auth_provider = provider
            user = legacy
    if user is None:
        existing = await get_user_by_email(db, email)
        if existing is not None:
            same_provider = existing.auth_provider == provider
            unbound = existing.external_subject is None
            if not (same_provider and email_verified and (unbound or rebind_on_verified_email)):
                raise ProvisioningRefused(
                    "This email belongs to an account that signs in another way."
                )
            user = existing

    if user is None:
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

    if not user.is_active:
        raise ProvisioningRefused("Account disabled.")
    user.name = name
    user.role = role
    user.external_subject = subject
    await db.commit()
    await db.refresh(user)
    return user
