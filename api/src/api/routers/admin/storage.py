import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_admin_user, get_db
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.models.workspace import Workspace
from api.schemas.storage_backend import StorageBackendCreate, StorageBackendOut

router = APIRouter(prefix="/storage-backends")

VALID_KINDS = {"local_fs", "nas", "s3", "adls_gen2"}


@router.get("", response_model=list[StorageBackendOut])
async def list_backends(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[StorageBackendOut]:
    result = await db.execute(select(StorageBackend))
    backends = result.scalars().all()
    out = []
    for sb in backends:
        count_result = await db.execute(
            select(func.count()).where(Workspace.storage_backend_id == sb.id)
        )
        count = count_result.scalar_one()
        out.append(
            StorageBackendOut(
                id=sb.id,
                kind=sb.kind,
                name=sb.name,
                root_uri=sb.root_uri,
                uc_storage_credential_id=sb.uc_storage_credential_id,
                created_by=sb.created_by,
                created_at=sb.created_at,
                workspace_count=count,
            )
        )
    return out


@router.post("", response_model=StorageBackendOut, status_code=status.HTTP_201_CREATED)
async def create_backend(
    body: StorageBackendCreate,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> StorageBackendOut:
    if body.kind not in VALID_KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"kind must be one of {sorted(VALID_KINDS)}",
        )
    sb = StorageBackend(
        kind=body.kind,
        name=body.name,
        root_uri=body.root_uri,
        uc_storage_credential_id=body.uc_storage_credential_id,
        created_by=admin.id,
    )
    db.add(sb)
    await db.commit()
    await db.refresh(sb)
    return StorageBackendOut(
        id=sb.id,
        kind=sb.kind,
        name=sb.name,
        root_uri=sb.root_uri,
        uc_storage_credential_id=sb.uc_storage_credential_id,
        created_by=sb.created_by,
        created_at=sb.created_at,
        workspace_count=0,
    )


@router.delete("/{backend_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backend(
    backend_id: uuid.UUID,
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    result = await db.execute(select(StorageBackend).where(StorageBackend.id == backend_id))
    sb = result.scalar_one_or_none()
    if sb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    count_result = await db.execute(
        select(func.count()).where(Workspace.storage_backend_id == backend_id)
    )
    if count_result.scalar_one() > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a backend that is in use by one or more workspaces",
        )
    await db.delete(sb)
    await db.commit()
