import secrets
import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.user import Credential, User

SESSION_TTL = timedelta(days=7)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


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


async def delete_session(db: AsyncSession, token: str) -> None:
    result = await db.execute(
        select(Credential).where(Credential.token == token, Credential.kind == "session")
    )
    cred = result.scalar_one_or_none()
    if cred:
        await db.delete(cred)
        await db.commit()
