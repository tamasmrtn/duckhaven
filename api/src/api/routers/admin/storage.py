import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, get_polaris_client, require_permission
from api.models.catalog import Catalog
from api.models.storage_backend import StorageBackend
from api.models.user import User
from api.schemas.storage_backend import (
    StorageBackendCreate,
    StorageBackendHealth,
    StorageBackendOut,
)
from api.services.permissions import Permission
from api.services.polaris import PolarisClient
from api.services.storage_health import validate_backend

router = APIRouter(prefix="/storage-backends")

VALID_KINDS = {"object_store", "s3", "adls_gen2"}


@router.get("", response_model=list[StorageBackendOut])
async def list_backends(
    admin: User = Depends(require_permission(Permission.STORAGE_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> list[StorageBackendOut]:
    """Every storage backend, with how many catalogs use each.

    The count is what makes a backend safe or unsafe to delete."""
    result = await db.execute(select(StorageBackend))
    backends = result.scalars().all()
    out = []
    for sb in backends:
        count_result = await db.execute(
            select(func.count()).where(Catalog.storage_backend_id == sb.id)
        )
        count = count_result.scalar_one()
        out.append(
            StorageBackendOut(
                id=sb.id,
                kind=sb.kind,
                name=sb.name,
                root_uri=sb.root_uri,
                config=sb.config,
                created_by=sb.created_by,
                created_at=sb.created_at,
                workspace_count=count,
            )
        )
    return out


@router.post("", response_model=StorageBackendOut, status_code=status.HTTP_201_CREATED)
async def create_backend(
    body: StorageBackendCreate,
    admin: User = Depends(require_permission(Permission.STORAGE_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> StorageBackendOut:
    """Register a storage backend catalogs can be created on.

    Credentials in `config` are stored for vending to agents and are never read
    back out through the API."""
    if body.kind not in VALID_KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"kind must be one of {sorted(VALID_KINDS)}",
        )
    sb = StorageBackend(
        kind=body.kind,
        name=body.name,
        root_uri=body.root_uri,
        config=body.config,
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
        config=sb.config,
        created_by=sb.created_by,
        created_at=sb.created_at,
        workspace_count=0,
    )


@router.post("/{backend_id}/health", response_model=StorageBackendHealth)
async def check_backend_health(
    backend_id: uuid.UUID,
    admin: User = Depends(require_permission(Permission.STORAGE_MANAGE)),
    db: AsyncSession = Depends(get_db),
    polaris: PolarisClient = Depends(get_polaris_client),
) -> StorageBackendHealth:
    """Validate that an external backend's vended credentials can reach storage."""
    result = await db.execute(select(StorageBackend).where(StorageBackend.id == backend_id))
    sb = result.scalar_one_or_none()
    if sb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return await validate_backend(polaris, sb)


@router.delete("/{backend_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backend(
    backend_id: uuid.UUID,
    admin: User = Depends(require_permission(Permission.STORAGE_MANAGE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a storage backend. Refused while any catalog still uses it.

    Removes only DuckHaven's registration -- nothing in the object store is
    touched."""
    result = await db.execute(select(StorageBackend).where(StorageBackend.id == backend_id))
    sb = result.scalar_one_or_none()
    if sb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    count_result = await db.execute(
        select(func.count()).where(Catalog.storage_backend_id == backend_id)
    )
    if count_result.scalar_one() > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete a backend that is in use by one or more catalogs",
        )
    await db.delete(sb)
    await db.commit()
