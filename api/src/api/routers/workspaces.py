import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db, get_polaris_client
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace, WorkspaceMember
from api.schemas.workspace import AddMemberRequest, MemberOut, WorkspaceCreate, WorkspaceOut
from api.services import catalog as catalog_service
from api.services.polaris import PolarisClient, PolarisError
from api.services.workspace import (
    assert_workspace_member,
    default_object_store_backend,
    derive_catalog_slug,
    get_default_catalog,
    get_workspace,
    mirror_member_grant,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces")


async def _workspace_out(db: AsyncSession, workspace: Workspace) -> WorkspaceOut:
    """Serialize a workspace, summarizing its default catalog's storage so the
    UI's backend badge keeps rendering after storage moved onto catalogs."""
    default = await get_default_catalog(db, workspace.id)
    return WorkspaceOut(
        id=workspace.id,
        slug=workspace.slug,
        name=workspace.name,
        created_at=workspace.created_at,
        default_catalog=default.slug if default is not None else None,
        storage_backend_id=default.storage_backend_id if default is not None else None,
        storage_backend_kind=(default.storage_backend.kind if default is not None else None),
    )


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceOut]:
    result = await db.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
    )
    return [await _workspace_out(db, ws) for ws in result.scalars().all()]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> WorkspaceOut:
    existing = await db.execute(select(Workspace).where(Workspace.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Slug already taken")
    # No backend chosen → auto-provision a bundled object-store backend so the
    # common case needs only a workspace name. A Polaris failure below rolls the
    # whole transaction back (this backend included).
    if body.storage_backend_id is None:
        backend = default_object_store_backend(body.name, user.id)
        db.add(backend)
        await db.flush()
    else:
        backend = await db.get(StorageBackend, body.storage_backend_id)
        if backend is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown storage backend"
            )
    ws = Workspace(slug=body.slug, name=body.name)
    db.add(ws)
    await db.flush()
    member = WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner")
    db.add(member)
    await db.flush()

    # Eagerly provision the workspace's default catalog (Polaris catalog named
    # after the workspace slug, for legacy parity) and attach it as default. If
    # Polaris is unreachable the whole transaction rolls back so the caller can
    # retry once Polaris is healthy (D7).
    try:
        catalog = await catalog_service.create_catalog(
            db,
            polaris,
            slug=await derive_catalog_slug(db, body.slug),
            name=body.name,
            backend=backend,
            created_by=user.id,
            polaris_name=body.slug,
        )
        await catalog_service.attach_catalog(
            db, workspace=ws, catalog=catalog, attached_by=user.id, make_default=True
        )
    except HTTPException:
        await db.rollback()
        raise
    except PolarisError as exc:
        logger.warning("Polaris provisioning failed for ws=%s; rolling back: %s", ws.slug, exc)
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Polaris provisioning failed: {exc}",
        ) from exc

    await db.commit()
    await db.refresh(ws)
    return await _workspace_out(db, ws)


@router.get("/{ws}", response_model=WorkspaceOut)
async def get_workspace_detail(
    ws: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceOut:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id)
    return await _workspace_out(db, workspace)


@router.get("/{ws}/members", response_model=list[MemberOut])
async def list_members(
    ws: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceMember]:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    result = await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace.id)
    )
    return list(result.scalars().all())


@router.post("/{ws}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    ws: str,
    body: AddMemberRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> WorkspaceMember:
    workspace = await get_workspace(db, ws)
    if workspace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await assert_workspace_member(db, workspace.id, user.id, min_role="owner")
    member = WorkspaceMember(workspace_id=workspace.id, user_id=body.user_id, role=body.role)
    db.add(member)
    await db.commit()
    await db.refresh(member)

    # Permission authority is the API boundary (D10); the catalog grant mirror
    # is a no-op (see services/workspace.mirror_member_grant).
    target = await db.get(User, body.user_id)
    if target is not None:
        await mirror_member_grant(polaris, workspace.slug, target.email, body.role)

    return member
