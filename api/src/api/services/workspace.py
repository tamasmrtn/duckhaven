import logging
import re
import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.config import settings
from api.models.assistant import AssistantConversation
from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.maintenance import MaintenanceRecommendation, TableHealthSample
from api.models.query import Query, SavedQuery, Schedule
from api.models.sql_session import SqlSession
from api.models.storage_backend import StorageBackend
from api.models.workspace import Workspace, WorkspaceMember
from api.services.polaris import (
    PolarisCatalog,
    PolarisClient,
    PolarisConflictError,
    PolarisError,
    PolarisNotFoundError,
)

logger = logging.getLogger(__name__)


class _Unset:
    """Sentinel distinguishing a PATCH field the caller omitted from one
    explicitly set to null (which should clear it)."""


UNSET = _Unset()

ROLE_ORDER = {"reader": 0, "writer": 1, "owner": 2}

# Backend kind → Polaris storage type. Every backend is object storage:
# object_store is physically backed by the bundled object-store bucket (S3);
# s3 / adls_gen2 are operator-owned external object stores.
_KIND_TO_STORAGE_TYPE = {
    "object_store": "S3",
    "s3": "S3",
    "adls_gen2": "AZURE",
}

# Kinds backed by the bundled bucket. Their root_uri is a prefix label under
# that bucket rather than a real storage URI.
_BUNDLED_STORE_KINDS = {"object_store"}


def _bundled_prefix(root_uri: str) -> str:
    """Normalise a local backend's root_uri into a bucket-relative prefix."""
    prefix = root_uri.strip()
    if "://" in prefix:
        prefix = prefix.split("://", 1)[1]
    return prefix.strip("/")


def _external_extra_storage(kind: str, config: dict | None) -> dict | None:
    """Build the Polaris storageConfigInfo extras for an external backend.

    Keys are the camelCase field names Polaris expects (roleArn / tenantId …);
    storageType + allowedLocations are added by the Polaris client. Every value
    is an identifier, never a static secret — Polaris vends short-lived scoped
    credentials by assuming the role / consenting app at attach time (I7).
    """
    if not config:
        return None
    if kind == "s3":
        extra: dict = {"roleArn": config["role_arn"], "region": config["region"]}
        if config.get("external_id"):
            extra["externalId"] = config["external_id"]
        if config.get("endpoint"):
            extra["endpoint"] = config["endpoint"]
        if config.get("path_style_access") is not None:
            extra["pathStyleAccess"] = config["path_style_access"]
        return extra
    if kind == "adls_gen2":
        extra = {"tenantId": config["tenant_id"]}
        if config.get("multi_tenant_app_name"):
            extra["multiTenantAppName"] = config["multi_tenant_app_name"]
        if config.get("consent_url"):
            extra["consentUrl"] = config["consent_url"]
        if config.get("hierarchical") is not None:
            extra["hierarchical"] = config["hierarchical"]
        return extra
    return None


def polaris_storage(
    kind: str, root_uri: str, config: dict | None = None
) -> tuple[str, str, dict | None]:
    """Resolve a backend's (Polaris storage type, base location, extra storage).

    object_store is backed by the bundled bucket: its root_uri is a prefix
    label under that bucket and the extra storage config carries the
    vended/internal endpoints. s3/adls_gen2 are external stores whose root_uri
    already carries a scheme; their extras (role ARN / tenant id / …) come from
    the backend's per-kind ``config``.
    """
    storage_type = _KIND_TO_STORAGE_TYPE.get(kind, "S3")
    if kind in _BUNDLED_STORE_KINDS:
        prefix = _bundled_prefix(root_uri)
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
    return storage_type, root_uri.rstrip("/"), _external_extra_storage(kind, config)


def default_object_store_backend(name: str, created_by: uuid.UUID) -> StorageBackend:
    """Build a bundled object-store backend at the bucket root for a
    name-only workspace. root_uri="" keeps the catalog base at the bucket root;
    per-workspace isolation comes from the `/{slug}` scope added in
    ensure_polaris_catalog. The caller adds and flushes the row."""
    return StorageBackend(
        kind="object_store",
        name=name,
        root_uri="",
        created_by=created_by,
    )


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


async def update_workspace(
    db: AsyncSession,
    workspace: Workspace,
    *,
    name: str | None,
    description: str | None | _Unset = UNSET,
) -> Workspace:
    """Rename and/or re-describe a workspace. Slug is not renameable here — it
    is the routable `/$ws/...` segment, and rescoping it risks breaking
    bookmarks and shared links.

    ``description`` defaults to ``UNSET`` (leave unchanged) rather than
    ``None``, so an explicit null in the request can still clear a
    previously-set description instead of being indistinguishable from
    "field omitted"."""
    if name is not None:
        workspace.name = name
    if description is not UNSET:
        workspace.description = description
    await db.commit()
    await db.refresh(workspace)
    return workspace


async def delete_workspace(db: AsyncSession, workspace: Workspace) -> None:
    """Permanently delete a workspace and its intrinsic control-plane rows
    (membership, schedules, query/session history, saved queries, assistant
    conversations, maintenance sidecars).

    Never touches Polaris or `Catalog` rows: catalogs are decoupled M:N and are
    explicitly designed to survive their owning workspace being removed, same
    as `detach_workspace_catalog` already does for a single detach. Every one
    of these tables has no `ondelete` on its `workspace_id` FK, so each needs
    an explicit pre-delete — `workspace_catalogs` is the only exception
    (already `cascade="all, delete-orphan"` on `Workspace.catalog_links`), and
    `agent_grants`/`semantic_models`/`lineage_edges` already cascade/null at
    the DB level.
    """
    for model in (
        WorkspaceMember,
        Schedule,
        Query,
        SavedQuery,
        SqlSession,
        AssistantConversation,
        TableHealthSample,
        MaintenanceRecommendation,
    ):
        await db.execute(delete(model).where(model.workspace_id == workspace.id))
    await db.delete(workspace)
    await db.commit()


async def get_workspace(db: AsyncSession, slug_or_id: str) -> Workspace | None:
    stmt = select(Workspace)
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


_BUNDLED_ENDPOINT_KEYS = ("endpoint", "endpointInternal")


async def _reconcile_bundled_endpoints(
    polaris: PolarisClient, existing: PolarisCatalog, extra_storage: dict | None
) -> None:
    """Bring an existing bundled catalog's stored endpoints back in line with config.

    Polaris records a catalog's storage config once, at creation, and vends it to
    DuckDB from then on. For the bundled store that config is derived entirely
    from ``settings``, so an operator who changes ``S3_ENDPOINT`` — or who moves
    the store to a different service name — leaves every existing catalog vending
    an address that no longer resolves. Reconciling here means the next browse
    heals it.

    Only the two endpoint keys are touched, and only when they actually differ, so
    this neither rewrites a hand-edited Polaris config nor burns an entity version
    on every browse. External backends are skipped: their endpoints come from the
    operator's own per-backend config, not from ours.
    """
    if not extra_storage or existing.entity_version is None:
        return
    desired = {k: extra_storage[k] for k in _BUNDLED_ENDPOINT_KEYS if k in extra_storage}
    if not desired or all(existing.storage_config.get(k) == v for k, v in desired.items()):
        return
    try:
        await polaris.update_catalog_storage(existing, {**existing.storage_config, **desired})
    except PolarisError:
        # A concurrent update (409) or a Polaris that refuses the edit must not
        # break browsing: the catalog still works at its recorded endpoint if that
        # endpoint still resolves, and the next browse retries.
        logger.warning(
            "Could not reconcile storage endpoints for catalog %r", existing.name, exc_info=True
        )


async def ensure_polaris_catalog(
    polaris: PolarisClient,
    polaris_name: str,
    *,
    storage_type: str,
    base_location: str,
    extra_storage: dict | None = None,
    default_schema: str = DEFAULT_SCHEMA,
) -> None:
    """Lazily create a catalog's Polaris catalog and default namespace, and
    grant the service principal data access on it.

    The catalog's base location is scoped per catalog (a `/{polaris_name}`
    suffix) so catalogs sharing a backend never collide on object-store paths.
    For catalogs migrated from the legacy 1:1 model `polaris_name` equals the
    originating workspace slug, so the location stays byte-identical (no Polaris
    rename). Idempotent: any PolarisConflictError from create is treated as
    success. Used by the catalog-create path and as a self-heal for browsing.

    An existing bundled catalog also has its endpoints reconciled — see
    ``_reconcile_bundled_endpoints``.
    """
    scoped_location = f"{base_location.rstrip('/')}/{polaris_name}"
    try:
        existing = await polaris.get_catalog(polaris_name)
    except PolarisNotFoundError:
        existing = None
    if existing is None:
        try:
            await polaris.create_catalog(
                polaris_name,
                storage_type=storage_type,
                base_location=scoped_location,
                extra_storage=extra_storage,
            )
        except PolarisConflictError:
            pass
    else:
        await _reconcile_bundled_endpoints(polaris, existing, extra_storage)
    # Wire data-access grants so the agent's DuckDB can read/write tables.
    await polaris.ensure_catalog_access(polaris_name)
    try:
        await polaris.create_schema(polaris_name, default_schema)
    except PolarisConflictError:
        pass


# Catalog slugs double as DuckDB ATTACH aliases and appear in
# `catalog.schema.table` SQL, so they must be identifier-safe.
_CATALOG_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_catalog_slug(slug: str) -> str:
    """Return ``slug`` if it is identifier-safe, else raise 422."""
    if not _CATALOG_SLUG_RE.match(slug) or len(slug) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Catalog slug must match ^[a-z][a-z0-9_]*$ (lowercase, "
                "start with a letter, only letters/digits/underscores)."
            ),
        )
    return slug


async def resolve_workspace_catalogs(db: AsyncSession, workspace_id: uuid.UUID) -> list[Catalog]:
    """All catalogs attached to a workspace, each with its storage backend loaded."""
    rows = await db.execute(
        select(Catalog)
        .join(WorkspaceCatalog, WorkspaceCatalog.catalog_id == Catalog.id)
        .where(WorkspaceCatalog.workspace_id == workspace_id)
        .options(selectinload(Catalog.storage_backend))
        .order_by(Catalog.slug)
    )
    return list(rows.scalars().all())


async def get_default_catalog(db: AsyncSession, workspace_id: uuid.UUID) -> Catalog | None:
    """The workspace's default catalog (the one `USE`d for unqualified names)."""
    row = await db.execute(
        select(Catalog)
        .join(WorkspaceCatalog, WorkspaceCatalog.catalog_id == Catalog.id)
        .where(
            WorkspaceCatalog.workspace_id == workspace_id,
            WorkspaceCatalog.is_default.is_(True),
        )
        .options(selectinload(Catalog.storage_backend))
    )
    return row.scalar_one_or_none()


async def resolve_catalog(db: AsyncSession, workspace_id: uuid.UUID, catalog_slug: str) -> Catalog:
    """Resolve a catalog by slug that is attached to ``workspace_id``; 404 otherwise."""
    row = await db.execute(
        select(Catalog)
        .join(WorkspaceCatalog, WorkspaceCatalog.catalog_id == Catalog.id)
        .where(
            WorkspaceCatalog.workspace_id == workspace_id,
            Catalog.slug == catalog_slug,
        )
        .options(selectinload(Catalog.storage_backend))
    )
    catalog = row.scalar_one_or_none()
    if catalog is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Catalog '{catalog_slug}' is not attached to this workspace.",
        )
    return catalog
