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
from api.routers.admin.service_accounts import SERVICE_ACCOUNT_PROVIDER
from api.schemas.auth import UserOut
from api.schemas.setup import FirstAdminRequest, SetupStatus
from api.services.auth import create_session, hash_password, set_session_cookie

router = APIRouter(prefix="/setup", tags=["setup"])


async def _human_user_count(db: AsyncSession) -> int:
    """Accounts a person could sign in with.

    Service accounts are excluded deliberately: they have no password and can only
    present a PAT, so one existing says nothing about whether a human can get in.
    The assistant creates one at startup, which on a fresh deployment would
    otherwise be the only `users` row — and would lock the operator out of first-run
    setup entirely.
    """
    result = await db.execute(
        select(func.count()).select_from(User).where(User.auth_provider != SERVICE_ACCOUNT_PROVIDER)
    )
    return int(result.scalar_one())


@router.get("/status", response_model=SetupStatus)
async def setup_status(db: AsyncSession = Depends(get_db)) -> SetupStatus:
    """Whether this deployment still needs its first admin. Unauthenticated.

    The SPA calls it before rendering the login screen, to decide between showing
    sign-in and showing first-run setup."""
    return SetupStatus(needs_admin=await _human_user_count(db) == 0)


@router.post("/admin", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_first_admin(
    body: FirstAdminRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    x_setup_token: str | None = Header(default=None, alias="X-Setup-Token"),
) -> User:
    """Create the first admin from the deployment's setup token, and sign them in.

    Authorized by the `X-Setup-Token` header rather than a session, because no
    account exists yet. The token is consumed on success and the endpoint returns
    409 once a human account exists, so it cannot be replayed to mint a second
    admin."""
    if await _human_user_count(db) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup already complete: an admin user exists.",
        )

    # A configured token wins over the file: where the container filesystem is
    # ephemeral, a self-generated one changes on every replica replacement.
    expected_token = settings.setup_token
    if not expected_token:
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
