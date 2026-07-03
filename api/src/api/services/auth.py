import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
from fastapi import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.config import settings
from api.models.user import Credential, User

# Session lifetime is operator-tunable via `session_max_age_seconds`; it drives
# both the DB credential expiry and the cookie max-age so the two never diverge.
SESSION_TTL = timedelta(seconds=settings.session_max_age_seconds)

SESSION_COOKIE = "session"


def set_session_cookie(response: Response, token: str) -> None:
    """Set the session cookie with the standard hardened flags. Shared by every
    path that establishes a session (local login, setup, OIDC, LDAP)."""
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_max_age_seconds,
    )


# Personal Access Tokens: a machine-caller credential presented as a bearer
# token. Format mirrors the agent bootstrap token (`dh_boot_...`): a scanning
# prefix plus a high-entropy random body. Unlike sessions (stored raw), a PAT is
# stored only as a SHA-256 digest — a fast hash is the right choice for a
# 256-bit-random secret (no brute-force surface) and, being deterministic, lets
# us look the credential up by the hash of a presented token.
PAT_PREFIX = "dh_pat_"


def generate_pat() -> str:
    return f"{PAT_PREFIX}{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def authenticate_password(db: AsyncSession, email: str, password: str) -> User | None:
    """Resolve a username/password submission to a user, or None.

    Local-first: a user that holds a local password is always verified against it
    — so the break-glass admin logs in even when the IdP/LDAP is unreachable. Only
    when there is no matching local password do we fall back to an LDAP bind (for
    federated or not-yet-provisioned accounts). OIDC is a separate redirect flow.
    """
    user = await get_user_by_email(db, email)
    if user is not None and user.password_hash is not None:
        if user.is_active and verify_password(password, user.password_hash):
            return user
        return None
    if settings.ldap_enabled:
        from api.services.ldap import authenticate_ldap

        return await authenticate_ldap(db, email, password)
    return None


async def create_session(db: AsyncSession, user_id: uuid.UUID) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(tz=UTC) + SESSION_TTL
    cred = Credential(user_id=user_id, kind="session", token=token, expires_at=expires_at)
    db.add(cred)
    await db.commit()
    return token


async def get_session_user(db: AsyncSession, token: str) -> User | None:
    now = datetime.now(tz=UTC)
    result = await db.execute(
        select(Credential)
        .options(selectinload(Credential.user))
        .where(
            Credential.token == token,
            Credential.kind == "session",
            (Credential.expires_at == None) | (Credential.expires_at > now),  # noqa: E711
        )
    )
    cred = result.scalar_one_or_none()
    if cred is None:
        return None
    return cred.user


async def get_pat_user(db: AsyncSession, token: str) -> User | None:
    """Resolve a presented PAT bearer token to its service-account user, or None.

    Parallels ``get_session_user`` but matches ``kind == "pat"`` against the
    SHA-256 hash of the token, so PATs and session cookies never cross-resolve.
    Rejects an expired token or one whose owning account is disabled.
    """
    if not token.startswith(PAT_PREFIX):
        return None
    now = datetime.now(tz=UTC)
    result = await db.execute(
        select(Credential)
        .options(selectinload(Credential.user))
        .where(
            Credential.token_hash == hash_token(token),
            Credential.kind == "pat",
            (Credential.expires_at == None) | (Credential.expires_at > now),  # noqa: E711
        )
    )
    cred = result.scalar_one_or_none()
    if cred is None:
        return None
    if cred.user is None or not cred.user.is_active:
        return None
    return cred.user


async def delete_session(db: AsyncSession, token: str) -> None:
    result = await db.execute(
        select(Credential).where(Credential.token == token, Credential.kind == "session")
    )
    cred = result.scalar_one_or_none()
    if cred:
        await db.delete(cred)
        await db.commit()
