import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.models.workspace import Workspace, WorkspaceMember
from api.services.polaris import PolarisClient, PolarisConflictError

logger = logging.getLogger(__name__)

ROLE_ORDER = {"reader": 0, "writer": 1, "owner": 2}

# Backend kind → Polaris storage type. local/NAS map to local-filesystem FILE
# storage; cloud backends map to their object-store type.
_KIND_TO_STORAGE_TYPE = {
    "local_fs": "FILE",
    "nas": "FILE",
    "s3": "S3",
    "adls_gen2": "AZURE",
}


def polaris_storage(kind: str, root_uri: str) -> tuple[str, str]:
    """Resolve a backend's (Polaris storage type, base location URI).

    FILE storage needs a `file://` URI; cloud roots already carry a scheme.
    """
    storage_type = _KIND_TO_STORAGE_TYPE.get(kind, "FILE")
    base = root_uri.rstrip("/")
    if "://" not in base:
        base = f"file://{base}"
    return storage_type, base


async def mirror_member_grant(
    polaris: PolarisClient, catalog: str, principal: str, role: str
) -> None:
    """No-op: DuckHaven is the sole permission authority (D10) and enforces
    membership at the API boundary. The old UC grant mirror was best-effort
    defense-in-depth only; Polaris RBAC wiring is intentionally out of scope."""
    return None


async def assert_workspace_member(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    min_role: str = "reader",
) -> WorkspaceMember:
    result = await db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    if ROLE_ORDER.get(member.role, -1) < ROLE_ORDER.get(min_role, 0):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return member


async def get_workspace(db: AsyncSession, slug_or_id: str) -> Workspace | None:
    stmt = select(Workspace).options(selectinload(Workspace.storage_backend))
    try:
        ws_id = uuid.UUID(slug_or_id)
        result = await db.execute(stmt.where(Workspace.id == ws_id))
    except ValueError:
        result = await db.execute(stmt.where(Workspace.slug == slug_or_id))
    return result.scalar_one_or_none()


# Default namespace created in every workspace catalog. NOT `main`: that name
# collides with DuckDB's built-in default schema in an attached catalog, which
# shadows the Iceberg namespace and makes `catalog.main.table` unresolvable.
DEFAULT_SCHEMA = "analytics"


async def ensure_polaris_catalog(
    polaris: PolarisClient,
    slug: str,
    *,
    storage_type: str,
    base_location: str,
    default_schema: str = DEFAULT_SCHEMA,
) -> None:
    """Lazily create the workspace's Polaris catalog and default namespace,
    and grant the service principal data access on it.

    Idempotent: any PolarisConflictError from create is treated as success.
    Used both by the eager `POST /workspaces` path (where the catalog won't
    exist yet) and as a self-heal for catalog browsing.
    """
    if not await polaris.catalog_exists(slug):
        try:
            await polaris.create_catalog(
                slug, storage_type=storage_type, base_location=base_location
            )
        except PolarisConflictError:
            pass
    # Wire data-access grants so the agent's DuckDB can read/write tables.
    await polaris.ensure_catalog_access(slug)
    try:
        await polaris.create_schema(slug, default_schema)
    except PolarisConflictError:
        pass
