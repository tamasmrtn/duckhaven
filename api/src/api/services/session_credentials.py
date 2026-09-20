"""Per-session credential vending (the seam).

This centralizes what a session's held DuckDB connection uses to reach Polaris and
where a load may stage bulk Parquet. Today it vends the API's single shared Polaris
service principal — the same identity the agent used from its own config — but it
moves the **vend point to the API**, which is the hook a future per-principal
Polaris identity (and real STS-scoped staging credentials) plugs into. Governance
today rests on the API's per-statement authorization (``grants.assert_query_access``)
plus the ``catalog_grants`` ACL, not on the Polaris token's identity.

DuckLake's credentials come from here too, and there it is the whole mechanism
rather than a hook: DuckLake has no credential vendor, so both the catalog
database login and the object-store credential are minted here and travel in the
dispatch payload, never reaching the agent's config or disk.

Deferred (env-gated integration tests): minting a distinct Polaris principal per
DuckHaven principal, and true short-lived STS credentials for the staging prefix
(the bundled backend has no STS — staging scoping there is the unique prefix
plus the statement policy that a ``COPY`` may only touch it).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from api.config import settings
from api.models import Catalog
from api.models.catalog import KIND_DUCKLAKE
from api.models.storage_backend import StorageBackend
from api.services.workspace import DEFAULT_SCHEMA, polaris_storage

# Long enough for a slow query, short enough that a leaked credential expires.
# Only the external backends mint scoped credentials; see `build_storage_block`.
DUCKLAKE_CREDENTIAL_TTL = timedelta(hours=1)


def build_polaris_block() -> dict[str, str]:
    """The Polaris connection block put in the OPEN_SESSION frame so the agent's
    session connection builds its iceberg SECRET from API-vended credentials rather
    than its own static config. (Same identity today — the future per-principal
    hook replaces this body.)"""
    return {
        "endpoint": settings.polaris_base_url,
        "client_id": settings.polaris_client_id,
        "client_secret": settings.polaris_client_secret,
    }


def staging_uri_for(catalog: Catalog, session_id: uuid.UUID) -> str | None:
    """The scoped object-storage staging prefix for a session, under the active
    catalog's storage root: ``<base>/<segment>/<session_id>/``. Returns ``None``
    when the backend has no usable base location.

    The base comes from ``polaris_storage`` (not the raw ``root_uri``) so the
    bundled ``object_store`` backend — whose ``root_uri`` is a bucket-relative
    prefix label, not a real URI — resolves to a real ``s3://<bucket>[/<prefix>]``
    location that can be presigned. External backends' ``root_uri`` already carries
    a scheme, so their base is unchanged."""
    backend = catalog.storage_backend
    _, base, _ = polaris_storage(backend.kind, backend.root_uri or "", backend.config)
    base = base.rstrip("/")
    if not base:
        return None
    return f"{base}/{settings.sql_session_staging_prefix_segment}/{session_id}/"


def staging_prefixes(staging_uri: str | None) -> list[str]:
    """The prefixes the statement policy admits for COPY/read_* in this session."""
    return [staging_uri] if staging_uri else []


def ducklake_data_path(catalog: Catalog) -> str:
    """Where a DuckLake catalog's Parquet lives: ``<backend base>/<slug>/``.

    Resolved through ``polaris_storage`` because the bundled backend's
    ``root_uri`` is a prefix label, not a URI. Per-slug, so catalogs sharing a
    backend never collide.
    """
    backend = catalog.storage_backend
    _, base, _ = polaris_storage(backend.kind, backend.root_uri or "", backend.config)
    base = base.rstrip("/")
    if not base:
        raise ValueError(f"Storage backend for catalog {catalog.slug} has no base location")
    return f"{base}/{catalog.slug}/"


def build_ducklake_meta_block() -> dict[str, str | int]:
    """Postgres connection details an agent needs to reach a DuckLake catalog.

    The restricted ``ducklake_agent`` role. Sent per dispatch, never written to
    agent disk, so rotating it touches no agent config.
    """
    return {
        "host": settings.ducklake_agent_host,
        "port": settings.ducklake_agent_port,
        "database": settings.ducklake_agent_database,
        "user": settings.ducklake_agent_user,
        "password": settings.ducklake_agent_password,
    }


def build_ducklake_options() -> dict[str, str]:
    """Catalog options DuckHaven owns, applied on attach.

    These are GLOBAL options persisted in ``ducklake_metadata``, not
    per-connection settings, so DuckHaven's configuration is authoritative for
    them the same way it is for ``data_path``. Re-applying an unchanged value
    writes no snapshot, so sending them every attach costs one upsert and keeps
    a catalog from drifting away from the deployment's settings.

    ``target_file_size`` is the writer's target, and the maintenance advisor
    scores small files against ``target_file_bytes``; the two are documented
    together because a disagreement produces confident, wrong advice.
    """
    return {
        "target_file_size": f"{settings.ducklake_target_file_size_mb}MB",
        "data_inlining_row_limit": str(settings.ducklake_data_inlining_row_limit),
    }


def build_storage_block(backend: StorageBackend, data_path: str) -> dict[str, object]:
    """Storage credentials for a DuckLake catalog's data path.

    ``s3`` mints a short-lived STS ``AssumeRole`` and ``adls_gen2`` a
    user-delegation SAS. ``object_store`` is weaker: the bundled store's static
    key, bounded only by ``SCOPE`` to this catalog's prefix and the connection's
    lifetime. Closing that needs a bundled STS endpoint, which is not wired up.
    """
    if backend.kind == "adls_gen2":
        return _adls_storage_block(data_path)
    return _s3_storage_block(backend, data_path)


def _duckdb_endpoint(url: str) -> tuple[str, bool]:
    """Split an endpoint URL into DuckDB's ``(host[:port], use_ssl)`` form.

    DuckDB wants a bare host, not a URL. An empty endpoint is real AWS, HTTPS.
    """
    if not url:
        return "", True
    parsed = urlparse(url)
    if not parsed.netloc:  # already bare, e.g. "objectstore:9000"
        return url, False
    return parsed.netloc, parsed.scheme == "https"


def _s3_storage_block(backend: StorageBackend, data_path: str) -> dict[str, object]:
    config = backend.config or {}
    block: dict[str, object] = {
        "type": "s3",
        # Scoped to this catalog's prefix; a shared connection can attach several.
        "scope": data_path,
        "url_style": "path" if backend.kind == "object_store" else "vhost",
    }
    if backend.kind == "object_store":
        endpoint, use_ssl = _duckdb_endpoint(settings.s3_endpoint)
        block.update(
            {
                "key_id": settings.s3_access_key,
                "secret": settings.s3_secret_key,
                "session_token": "",
                "region": settings.s3_region,
                "endpoint": endpoint,
                "use_ssl": use_ssl,
            }
        )
        return block

    # External S3: assume the backend's role for scoped credentials.
    import boto3

    assume_kwargs: dict[str, object] = {
        "RoleArn": config["role_arn"],
        "RoleSessionName": f"dh-ducklake-{datetime.now(tz=UTC):%Y%m%d%H%M%S}",
        "DurationSeconds": int(DUCKLAKE_CREDENTIAL_TTL.total_seconds()),
    }
    if config.get("external_id"):
        assume_kwargs["ExternalId"] = config["external_id"]
    creds = boto3.client("sts").assume_role(**assume_kwargs)["Credentials"]
    endpoint, use_ssl = _duckdb_endpoint(config.get("endpoint") or "")
    block.update(
        {
            "key_id": creds["AccessKeyId"],
            "secret": creds["SecretAccessKey"],
            "session_token": creds["SessionToken"],
            "region": config.get("region") or settings.s3_region,
            "endpoint": endpoint,
            "use_ssl": use_ssl,
        }
    )
    if config.get("path_style_access"):
        block["url_style"] = "path"
    return block


def _adls_storage_block(data_path: str) -> dict[str, object]:
    """A container-scoped user-delegation SAS for the catalog's data path.

    Container-level, not per-blob: an agent writing a DuckLake table creates
    blob names DuckHaven has never seen.
    """
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import (
        BlobServiceClient,
        ContainerSasPermissions,
        generate_container_sas,
    )

    parsed = urlparse(data_path)
    container = parsed.username or parsed.netloc.split("@")[0]
    account_host = parsed.hostname or ""
    account = account_host.split(".", 1)[0]
    account_url = f"https://{account_host.replace('.dfs.', '.blob.')}"

    start = datetime.now(tz=UTC) - timedelta(minutes=1)  # clock-skew slack
    expiry = datetime.now(tz=UTC) + DUCKLAKE_CREDENTIAL_TTL
    service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
    delegation_key = service.get_user_delegation_key(key_start_time=start, key_expiry_time=expiry)
    sas = generate_container_sas(
        account_name=account,
        container_name=container,
        user_delegation_key=delegation_key,
        permission=ContainerSasPermissions(
            read=True, write=True, create=True, delete=True, list=True
        ),
        start=start,
        expiry=expiry,
    )
    return {
        "type": "azure",
        "account_name": account,
        "connection_string": f"BlobEndpoint={account_url};SharedAccessSignature={sas}",
    }


# Minted storage blocks by catalog id, with their expiry. Re-minted before the
# credential itself expires, so a query cannot lose it mid-scan.
_storage_cache: dict[uuid.UUID, tuple[float, dict[str, object]]] = {}
_STORAGE_CACHE_TTL_S = DUCKLAKE_CREDENTIAL_TTL.total_seconds() - 600


def reset_storage_cache() -> None:
    """Drop every cached credential. For tests, and for a settings change."""
    _storage_cache.clear()


async def _storage_block(catalog: Catalog, data_path: str) -> dict[str, object]:
    """The catalog's storage credential, minted at most once per TTL.

    Minting is a network round-trip on the dispatch path, so it runs on a thread
    and is cached. The bundled store touches no network and skips both.
    """
    backend = catalog.storage_backend
    if backend.kind == "object_store":
        return build_storage_block(backend, data_path)

    now = time.monotonic()
    cached = _storage_cache.get(catalog.id)
    if cached is not None and now < cached[0]:
        return cached[1]
    block = await asyncio.to_thread(build_storage_block, backend, data_path)
    _storage_cache[catalog.id] = (now + _STORAGE_CACHE_TTL_S, block)
    return block


async def build_catalog_attach(catalog: Catalog) -> dict[str, object]:
    """Everything an agent needs to ATTACH one catalog.

    The single wire description used by both dispatch paths (one-shot queries
    and SQL sessions). Iceberg catalogs carry no credentials — the agent's
    config supplies Polaris, which vends storage creds on attach. DuckLake
    carries a Postgres block and a scoped storage block, minted here.
    """
    entry: dict[str, object] = {
        "slug": catalog.slug,
        "kind": catalog.kind,
        "polaris_name": catalog.polaris_name or "",
        "backend": {
            "kind": catalog.storage_backend.kind,
            "root_uri": catalog.storage_backend.root_uri,
        },
        "default_schema": DEFAULT_SCHEMA,
    }
    if catalog.kind != KIND_DUCKLAKE:
        return entry

    data_path = ducklake_data_path(catalog)
    entry.update(
        {
            "data_path": data_path,
            "metadata_schema": catalog.metadata_schema,
            "meta": build_ducklake_meta_block(),
            "options": build_ducklake_options(),
            "storage": await _storage_block(catalog, data_path),
        }
    )
    return entry
