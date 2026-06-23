"""Browser-driven first-admin onboarding.

Status endpoint is unauthenticated and idempotent — the SPA polls it on boot
to decide whether to route to /setup. The create endpoint is gated by a
one-shot token written on first boot (see deploy/api-entrypoint.sh); the token
file is deleted after the admin is successfully created so it cannot be replayed.
"""

import secrets
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.deps import get_db, get_polaris_client
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.schemas.auth import UserOut
from api.schemas.setup import FirstAdminRequest, SetupStatus, SystemStorageChoice
from api.services.auth import create_session, hash_password
from api.services.polaris import PolarisClient
from api.services.system_catalog.bootstrap import provision_system_catalog

router = APIRouter(prefix="/setup", tags=["setup"])

_VALID_STORAGE_KINDS = {"object_store", "s3", "adls_gen2"}


def _system_backend(choice: SystemStorageChoice, created_by: uuid.UUID) -> StorageBackend:
    """Build (unpersisted) the system catalog's storage backend from the admin's
    setup choice. External object stores must carry a root_uri."""
    if choice.kind not in _VALID_STORAGE_KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"kind must be one of {sorted(_VALID_STORAGE_KINDS)}",
        )
    if choice.kind != "object_store" and not choice.root_uri.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"root_uri is required for kind '{choice.kind}'",
        )
    return StorageBackend(
        kind=choice.kind,
        name=choice.name or "System",
        root_uri=choice.root_uri,
        uc_storage_credential_id=choice.uc_storage_credential_id,
        created_by=created_by,
    )


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
    polaris: PolarisClient = Depends(get_polaris_client),
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

    # Validate the system-catalog storage choice up front so a bad choice fails
    # before any user is created (no half-finished setup).
    _system_backend(body.system_storage, created_by=uuid.uuid4())

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
        role="admin",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Provision the built-in system catalog on the admin's chosen storage. The
    # Postgres rows persist even if Polaris is unreachable now (startup self-heal
    # completes it later), so a Polaris hiccup never blocks finishing setup.
    backend = _system_backend(body.system_storage, created_by=user.id)
    await provision_system_catalog(db, polaris, backend=backend, created_by=user.id)

    # Consume the token so it cannot be replayed.
    settings.setup_token_path.unlink(missing_ok=True)

    token = await create_session(db, user.id)
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=7 * 24 * 3600,
    )
    return user
