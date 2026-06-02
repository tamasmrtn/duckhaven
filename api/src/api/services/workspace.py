import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.config import settings
from api.models.workspace import Workspace, WorkspaceMember
from api.services.polaris import PolarisClient, PolarisConflictError

logger = logging.getLogger(__name__)

ROLE_ORDER = {"reader": 0, "writer": 1, "owner": 2}

# Backend kind → Polaris storage type. Every backend is object storage: local_fs
# and nas are physically backed by the bundled MinIO bucket (S3); s3 / adls_gen2
# are operator-owned external object stores.
_KIND_TO_STORAGE_TYPE = {
    "local_fs": "S3",
    "nas": "S3",
    "s3": "S3",
    "adls_gen2": "AZURE",
}

# Kinds backed by the bundled MinIO bucket. Their root_uri is a prefix label
# under that bucket rather than a real storage URI.
_BUNDLED_MINIO_KINDS = {"local_fs", "nas"}


def _minio_prefix(root_uri: str) -> str:
    """Normalise a local backend's root_uri into a bucket-relative prefix."""
    prefix = root_uri.strip()
    if "://" in prefix:
        prefix = prefix.split("://", 1)[1]
    return prefix.strip("/")


def polaris_storage(kind: str, root_uri: str) -> tuple[str, str, dict | None]:
    """Resolve a backend's (Polaris storage type, base location, extra storage).

    local_fs/nas are backed by the bundled MinIO bucket: their root_uri is a
    prefix label under that bucket and the extra storage config carries the
    vended/internal endpoints. s3/adls_gen2 are external stores whose root_uri
    already carries a scheme; they get no MinIO endpoint injected.
    """
    storage_type = _KIND_TO_STORAGE_TYPE.get(kind, "S3")
    if kind in _BUNDLED_MINIO_KINDS:
        prefix = _minio_prefix(root_uri)
        base = f"s3://{settings.s3_bucket}"
        if prefix:
            base = f"{base}/{prefix}"
        extra = {
            "endpoint": settings.s3_endpoint,
            "endpointInternal": settings.s3_endpoint_internal,
            "pathStyleAccess": True,
            "region": settings.s3_region,
        }
        return storage_type, base, extra
    return storage_type, root_uri.rstrip("/"), None


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
    extra_storage: dict | None = None,
    default_schema: str = DEFAULT_SCHEMA,
) -> None:
    """Lazily create the workspace's Polaris catalog and default namespace,
    and grant the service principal data access on it.

    The catalog's base location is scoped per workspace (a `/{slug}` suffix) so
    workspaces sharing a backend never collide on object-store paths.
    Idempotent: any PolarisConflictError from create is treated as success.
    Used both by the eager `POST /workspaces` path (where the catalog won't
    exist yet) and as a self-heal for catalog browsing.
    """
    scoped_location = f"{base_location.rstrip('/')}/{slug}"
    if not await polaris.catalog_exists(slug):
        try:
            await polaris.create_catalog(
                slug,
                storage_type=storage_type,
                base_location=scoped_location,
                extra_storage=extra_storage,
            )
        except PolarisConflictError:
            pass
    # Wire data-access grants so the agent's DuckDB can read/write tables.
    await polaris.ensure_catalog_access(slug)
    try:
        await polaris.create_schema(slug, default_schema)
    except PolarisConflictError:
        pass
