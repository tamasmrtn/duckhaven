"""Browser-driven first-admin onboarding.

Status endpoint is unauthenticated and idempotent — the SPA polls it on boot
to decide whether to route to /setup. The create endpoint is gated by a
one-shot token written on first boot (see deploy/api-entrypoint.sh); the token
file is deleted after the admin is successfully created so it cannot be replayed.
"""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.deps import get_db
from api.models.user import User
from api.schemas.auth import UserOut
from api.schemas.setup import FirstAdminRequest, SetupStatus
from api.services.auth import create_session, hash_password, set_session_cookie

router = APIRouter(prefix="/setup", tags=["setup"])


async def _user_count(db: AsyncSession) -> int:
    result = await db.execute(select(func.count()).select_from(User))
    return int(result.scalar_one())


@router.get("/status", response_model=SetupStatus)
async def setup_status(db: AsyncSession = Depends(get_db)) -> SetupStatus:
    return SetupStatus(needs_admin=await _user_count(db) == 0)


@router.post("/admin", response_model=UserOut)
async def create_first_admin(
    body: FirstAdminRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    x_setup_token: str | None = Header(default=None, alias="X-Setup-Token"),
) -> User:
    if await _user_count(db) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup already complete: an admin user exists.",
        )

    try:
        expected_token = settings.setup_token_path.read_text().strip()
    except FileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Setup token not available. The stack may need a fresh init.",
        ) from None

    if not x_setup_token or not secrets.compare_digest(x_setup_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing setup token.",
        )

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        role="admin",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Consume the token so it cannot be replayed.
    settings.setup_token_path.unlink(missing_ok=True)

    token = await create_session(db, user.id)
    set_session_cookie(response, token)
    return user
