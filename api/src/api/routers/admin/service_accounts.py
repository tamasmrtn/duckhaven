import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Query as QueryParam  # `Query` here is the model
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, require_permission
from api.models.query import Query
from api.models.rbac import Role
from api.models.user import Credential, User
from api.models.workspace import WorkspaceMember
from api.schemas.page import Page
from api.schemas.service_account import (
    CreateServiceAccountRequest,
    PatCreateRequest,
    PatOut,
    PatTokenOut,
    ServiceAccountOut,
    UpdateServiceAccountRequest,
)
from api.services.auth import generate_pat, get_user_by_email, hash_token
from api.services.paging import paginate
from api.services.permissions import Permission

router = APIRouter(prefix="/service-accounts")

# Service accounts are User rows with this provider: no local password, so they
# can never complete a password/OIDC/LDAP login — only present a PAT.
SERVICE_ACCOUNT_PROVIDER = "service_account"


def _synthesize_email(name: str) -> str:
    """Derive a unique-ish email for the account from its name. ``User.email`` is
    UNIQUE/NOT NULL; a service account has no mailbox, so we mint a stable
    address in a reserved domain. Collisions surface as a 409 at create time."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "service-account"
    return f"{slug}@service-account.local"


async def _assert_role_exists(db: AsyncSession, name: str) -> None:
    result = await db.execute(select(Role.id).where(Role.name == name))
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Unknown role: {name}"
        )


async def _active_admin_count(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
    )
    return result.scalar_one()


async def _get_service_account_or_404(db: AsyncSession, service_account_id: uuid.UUID) -> User:
    user = await db.get(User, service_account_id)
    if user is None or user.auth_provider != SERVICE_ACCOUNT_PROVIDER:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return user


async def _pat_counts(db: AsyncSession, sa_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not sa_ids:
        return {}
    result = await db.execute(
        select(Credential.user_id, func.count())
        .where(Credential.kind == "pat", Credential.user_id.in_(sa_ids))
        .group_by(Credential.user_id)
    )
    return {user_id: count for user_id, count in result.all()}


@router.get("", response_model=Page[ServiceAccountOut])
async def list_service_accounts(
    limit: int = QueryParam(default=100, ge=1, le=1000),
    cursor: str | None = QueryParam(default=None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.SERVICE_ACCOUNTS_MANAGE)),
) -> Page[ServiceAccountOut]:
    """Every service account, oldest first.

    A service account is a user with no password, reachable only through the
    tokens issued to it, so it runs through the same permission checks a person
    does."""
    rows, next_cursor, has_more = await paginate(
        db,
        select(User).where(User.auth_provider == SERVICE_ACCOUNT_PROVIDER),
        sort_columns=[User.created_at, User.id],
        limit=limit,
        cursor=cursor,
        descending=False,
    )
    accounts = [r[0] for r in rows]
    counts = await _pat_counts(db, [a.id for a in accounts])
    return Page[ServiceAccountOut](
        items=[
            ServiceAccountOut(
                id=a.id,
                name=a.name,
                email=a.email,
                role=a.role,
                is_active=a.is_active,
                created_at=a.created_at,
                pat_count=counts.get(a.id, 0),
            )
            for a in accounts
        ],
        cursor=next_cursor,
        has_more=has_more,
    )


@router.post("", response_model=ServiceAccountOut, status_code=status.HTTP_201_CREATED)
async def create_service_account(
    body: CreateServiceAccountRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.SERVICE_ACCOUNTS_MANAGE)),
) -> ServiceAccountOut:
    """Create a service account: a non-human principal with no password that
    authenticates only via PATs. Defaults to the zero-permission ``user`` role."""
    await _assert_role_exists(db, body.role)
    email = _synthesize_email(body.name)
    if await get_user_by_email(db, email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A service account with this name already exists.",
        )
    account = User(
        email=email,
        name=body.name,
        password_hash=None,
        role=body.role,
        auth_provider=SERVICE_ACCOUNT_PROVIDER,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return ServiceAccountOut(
        id=account.id,
        name=account.name,
        email=account.email,
        role=account.role,
        is_active=account.is_active,
        created_at=account.created_at,
        pat_count=0,
    )


@router.patch("/{service_account_id}", response_model=ServiceAccountOut)
async def update_service_account(
    service_account_id: uuid.UUID,
    body: UpdateServiceAccountRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.SERVICE_ACCOUNTS_MANAGE)),
) -> ServiceAccountOut:
    """Change a service account's global role and/or active state. Disabling
    (``is_active=False``) immediately blocks all of its PATs. Guards the
    last-active-admin the same way user management does."""
    account = await _get_service_account_or_404(db, service_account_id)

    demoting_admin = account.role == "admin" and body.role is not None and body.role != "admin"
    deactivating_admin = account.role == "admin" and body.is_active is False
    if (demoting_admin or deactivating_admin) and await _active_admin_count(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot remove the last active admin.",
        )

    if body.role is not None:
        await _assert_role_exists(db, body.role)
        account.role = body.role
    if body.is_active is not None:
        account.is_active = body.is_active
    await db.commit()
    await db.refresh(account)
    counts = await _pat_counts(db, [account.id])
    return ServiceAccountOut(
        id=account.id,
        name=account.name,
        email=account.email,
        role=account.role,
        is_active=account.is_active,
        created_at=account.created_at,
        pat_count=counts.get(account.id, 0),
    )


@router.delete("/{service_account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service_account(
    service_account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.SERVICE_ACCOUNTS_MANAGE)),
) -> None:
    """Hard-delete a service account (its PATs cascade). Workspace memberships,
    which have no FK cascade, are removed first. If the account still has audit
    history (queries, saved queries, schedules it created), deletion would orphan
    that trail, so we refuse with a 409 and the operator disables it instead."""
    account = await _get_service_account_or_404(db, service_account_id)
    # Refuse if the account has audit history (primary case: queries it ran) so
    # deletion never orphans the audit trail; the operator disables it instead.
    query_count = await db.execute(
        select(func.count()).select_from(Query).where(Query.user_id == service_account_id)
    )
    if query_count.scalar_one() > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Service account has history and cannot be deleted; disable it instead.",
        )
    await db.execute(delete(WorkspaceMember).where(WorkspaceMember.user_id == service_account_id))
    await db.delete(account)
    try:
        await db.commit()
    except IntegrityError as exc:
        # Backstop for other created-by references (saved queries, schedules)
        # that the DB enforces in production.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Service account has history and cannot be deleted; disable it instead.",
        ) from exc


@router.post(
    "/{service_account_id}/pat", response_model=PatTokenOut, status_code=status.HTTP_201_CREATED
)
async def issue_pat(
    service_account_id: uuid.UUID,
    body: PatCreateRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.SERVICE_ACCOUNTS_MANAGE)),
) -> PatTokenOut:
    """Issue a PAT for the service account. The raw secret is returned here once
    and only its SHA-256 hash is stored."""
    account = await _get_service_account_or_404(db, service_account_id)
    token = generate_pat()
    expires_at = (
        None
        if body.expires_in_days is None
        else datetime.now(tz=UTC) + timedelta(days=body.expires_in_days)
    )
    cred = Credential(
        user_id=account.id,
        kind="pat",
        token=None,
        token_hash=hash_token(token),
        expires_at=expires_at,
    )
    db.add(cred)
    await db.commit()
    await db.refresh(cred)
    return PatTokenOut(id=cred.id, token=token, expires_at=expires_at)


@router.get("/{service_account_id}/pats", response_model=list[PatOut])
async def list_pats(
    service_account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.SERVICE_ACCOUNTS_MANAGE)),
) -> list[Credential]:
    """The tokens issued to a service account: label, creation and expiry.

    The token values are not stored and cannot be listed -- a token is shown once,
    when it is issued."""
    await _get_service_account_or_404(db, service_account_id)
    result = await db.execute(
        select(Credential)
        .where(Credential.user_id == service_account_id, Credential.kind == "pat")
        .order_by(Credential.created_at)
    )
    return list(result.scalars().all())


@router.delete("/{service_account_id}/pat/{pat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_pat(
    service_account_id: uuid.UUID,
    pat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission(Permission.SERVICE_ACCOUNTS_MANAGE)),
) -> None:
    """Revoke one token. It stops authenticating immediately.

    Revoking a token leaves the service account and its other tokens alone; to
    stop all of them at once, deactivate the account."""
    await _get_service_account_or_404(db, service_account_id)
    result = await db.execute(
        select(Credential).where(
            Credential.id == pat_id,
            Credential.user_id == service_account_id,
            Credential.kind == "pat",
        )
    )
    cred = result.scalar_one_or_none()
    if cred is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await db.delete(cred)
    await db.commit()
