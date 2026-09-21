"""Validate that a storage backend actually reaches storage.

The bundled ``object_store`` is checked directly: the API LISTs its bucket with
the static credentials it is configured with, which is the same key Polaris
vends there. That catches a store that is down, a bucket that was never
created, and a mis-set credential.

For an external backend the check provisions a throwaway Polaris catalog from
the backend's config and creates a tiny Iceberg table — forcing Polaris to
assume the role / consent the app and write metadata to the operator's
storage. It then asks Polaris to vend
short-lived scoped client credentials (the same path agents use) and uses them
to LIST the probe location from the API. Any failure surfaces as
``valid=False`` with a sanitized detail; everything is cleaned up best-effort.

The cloud SDKs (boto3 / azure-storage-blob) are imported lazily so they are only
loaded when a health check actually runs.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from urllib.parse import urlparse

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.models.catalog import KIND_ICEBERG_POLARIS, Catalog
from api.models.storage_backend import StorageBackend
from api.schemas.storage_backend import StorageBackendHealth
from api.services.polaris import PolarisClient, PolarisError
from api.services.workspace import polaris_storage

logger = logging.getLogger(__name__)

_PROBE_SCHEMA = "dh_health"
_PROBE_TABLE = "probe"
# A single nullable int column — the table holds no data; creating it is what
# exercises the storage write path.
_PROBE_COLUMNS = [{"id": 1, "name": "x", "required": False, "type": "int"}]


def _short(exc: object) -> str:
    """One-line, secret-free error detail for surfacing in the admin UI."""
    msg = " ".join(str(exc).split())
    # Some SDK exceptions stringify to nothing; fall back to the type so the
    # detail is never blank.
    if not msg and isinstance(exc, BaseException):
        msg = type(exc).__name__
    return msg[:300]


def _block_to_creds(block: dict, location: str) -> dict:
    """Translate a DuckDB-dialect storage block into the spelling `_list_prefix` reads.

    Two credential vocabularies exist in this codebase: Polaris vends the
    Iceberg REST spelling (``s3.access-key-id``), and
    ``session_credentials.build_storage_block`` mints the same access in
    DuckDB's (``key_id``). The listing helpers already speak the first, so a
    DuckLake-era block is translated rather than given a second code path.
    """
    if block.get("type") == "azure":
        # `_list_adls` wants a bare SAS keyed by account, not a connection string.
        conn = str(block.get("connection_string") or "")
        sas = next(
            (
                part.split("=", 1)[1]
                for part in conn.split(";")
                if part.startswith("SharedAccessSignature=")
            ),
            "",
        )
        return {f"adls.sas-token.{block.get('account_name', '')}": sas}

    endpoint = str(block.get("endpoint") or "")
    if endpoint and "://" not in endpoint:
        # `_duckdb_endpoint` strips the scheme; boto3 wants it back.
        endpoint = f"{'https' if block.get('use_ssl') else 'http'}://{endpoint}"
    return {
        "s3.access-key-id": block.get("key_id"),
        "s3.secret-access-key": block.get("secret"),
        "s3.session-token": block.get("session_token") or None,
        "client.region": block.get("region"),
        "s3.endpoint": endpoint or None,
    }


async def validate_backend_direct(backend: StorageBackend) -> StorageBackendHealth:
    """Validate a backend without Polaris, by minting credentials ourselves.

    The external path below provisions a throwaway Polaris catalog, which a
    DuckLake-only deployment cannot do -- it does not run Polaris. That is the
    topology `docker-compose.ducklake-only.yml` ships, so registering an
    external backend there failed on a dependency the deployment deliberately
    removed.

    This exercises the same STS AssumeRole / user-delegation-SAS path a DuckLake
    attach uses, which is the thing actually worth proving.
    """
    from api.services.session_credentials import build_storage_block

    if backend.kind == "object_store":
        return _validate_bundled()

    try:
        # Inside the try: a backend saved with a missing config key raises here,
        # and an unusable backend is what this function reports, not raises.
        _, base_location, _ = polaris_storage(backend.kind, backend.root_uri, backend.config)
        probe = f"{base_location.rstrip('/')}/dhhealth{uuid.uuid4().hex[:12]}/"
        # Minting is a synchronous SDK call; off the event loop, as elsewhere.
        block = await asyncio.to_thread(build_storage_block, backend, probe)
        creds = _block_to_creds(block, probe)
        # A prefix with nothing under it lists zero objects rather than failing,
        # so this proves reach without needing the probe to have written.
        count = await asyncio.to_thread(
            _list_prefix, backend.kind, probe, creds, backend.config or {}
        )
    except Exception as exc:  # noqa: BLE001 — any failure means the backend isn't usable
        logger.warning(
            "Direct storage health check failed for backend=%s", backend.id, exc_info=True
        )
        return StorageBackendHealth(valid=False, detail=_short(exc))
    return StorageBackendHealth(
        valid=True,
        detail=f"Minted credentials reached storage ({count} object(s) under the probe path).",
    )


async def validate_backend_for(
    db: AsyncSession, polaris: PolarisClient, backend: StorageBackend
) -> StorageBackendHealth:
    """Validate through whichever path this deployment can actually run.

    Asked of the database rather than a flag, matching `readyz`: a deployment
    with no Iceberg catalog does not run Polaris, so the throwaway-catalog probe
    below would fail on a dependency that is deliberately absent.
    """
    has_iceberg = await db.scalar(
        select(sa.func.count()).select_from(Catalog).where(Catalog.kind == KIND_ICEBERG_POLARIS)
    )
    if not has_iceberg:
        return await validate_backend_direct(backend)
    return await validate_backend(polaris, backend)


async def validate_backend(polaris: PolarisClient, backend: StorageBackend) -> StorageBackendHealth:
    """Validate storage access end to end.

    The bundled ``object_store`` takes the short path below: it has no role to
    assume and no app to consent, so provisioning a throwaway catalog would
    prove nothing the LIST does not.
    """
    if backend.kind == "object_store":
        return _validate_bundled()

    storage_type, base_location, extra = polaris_storage(
        backend.kind, backend.root_uri, backend.config
    )
    temp = f"dhhealth{uuid.uuid4().hex[:12]}"
    # Scope the probe under a unique sub-prefix (mirrors ensure_polaris_catalog's
    # /{polaris_name} scoping) so its allowedLocations never overlap an existing
    # catalog already provisioned under the same backend root.
    probe_location = f"{base_location.rstrip('/')}/{temp}"
    try:
        await polaris.create_catalog(
            temp, storage_type=storage_type, base_location=probe_location, extra_storage=extra
        )
    except PolarisError as exc:
        return StorageBackendHealth(
            valid=False, detail=f"Polaris rejected the storage config: {_short(exc)}"
        )

    try:
        await polaris.ensure_catalog_access(temp)
        await polaris.create_schema(temp, _PROBE_SCHEMA)
        await polaris.create_table(
            catalog=temp, schema=_PROBE_SCHEMA, name=_PROBE_TABLE, columns=_PROBE_COLUMNS
        )
        body = await polaris.load_table_with_credentials(temp, _PROBE_SCHEMA, _PROBE_TABLE)
        creds = body.get("config") or {}
        location = (body.get("metadata") or {}).get("location") or probe_location
        count = _list_prefix(backend.kind, location, creds, backend.config or {})
        return StorageBackendHealth(
            valid=True,
            detail=f"Vended credentials reached storage ({count} object(s) under the probe path).",
        )
    except Exception as exc:  # noqa: BLE001 — any failure means the backend isn't usable
        logger.warning("Storage health check failed for backend=%s", backend.id, exc_info=True)
        return StorageBackendHealth(valid=False, detail=_short(exc))
    finally:
        await _cleanup(polaris, temp)


async def _cleanup(polaris: PolarisClient, catalog: str) -> None:
    """Best-effort teardown of the probe table, role and catalog."""
    try:
        await polaris.delete_table(catalog, _PROBE_SCHEMA, _PROBE_TABLE, purge=True)
    except PolarisError:
        pass
    try:
        await polaris.delete_schema(catalog, _PROBE_SCHEMA)
    except PolarisError:
        pass
    try:
        await polaris.delete_catalog_access(catalog)
    except PolarisError:
        pass
    try:
        await polaris.delete_catalog(catalog)
    except PolarisError:
        pass


def _validate_bundled() -> StorageBackendHealth:
    """LIST the bundled bucket with the API's own static credentials.

    There is no credential vending to exercise here — Polaris hands the bundled
    store the same static key the API holds — so the honest check is simply
    whether that key reaches the bucket. A failure means the store is down, the
    bucket was never created, or the configured key is wrong; all three are
    invisible to the operator today.
    """
    creds = {
        "s3.endpoint": settings.s3_endpoint_internal,
        "s3.access-key-id": settings.s3_access_key,
        "s3.secret-access-key": settings.s3_secret_key,
        "client.region": settings.s3_region,
    }
    try:
        count = _list_s3(f"s3://{settings.s3_bucket}/", creds, {})
    except Exception as exc:  # noqa: BLE001 - any SDK/transport failure is "unhealthy"
        logger.warning("Bundled object store health check failed: %s", exc)
        return StorageBackendHealth(
            valid=False, detail=f"Could not reach the bundled bucket: {_short(exc)}"
        )
    return StorageBackendHealth(
        valid=True, detail=f"Reached the bundled bucket ({count} object(s) at its root)."
    )


def _list_prefix(kind: str, location: str, creds: dict, config: dict) -> int:
    """LIST the probe location with the vended credentials; return object count."""
    if kind == "s3":
        return _list_s3(location, creds, config)
    if kind == "adls_gen2":
        return _list_adls(location, creds)
    raise ValueError(f"Unsupported backend kind for health check: {kind}")


def _list_s3(location: str, creds: dict, config: dict) -> int:
    import boto3

    parsed = urlparse(location)
    bucket, prefix = parsed.netloc, parsed.path.lstrip("/")
    client_kwargs: dict[str, str] = {}
    # Prefer the endpoint/region Polaris vends; fall back to the backend's own
    # config (S3-compatible stores often omit them from the vended creds).
    if endpoint := (creds.get("s3.endpoint") or config.get("endpoint")):
        client_kwargs["endpoint_url"] = endpoint
    if region := (creds.get("client.region") or creds.get("s3.region") or config.get("region")):
        client_kwargs["region_name"] = region
    s3 = boto3.client(
        "s3",
        aws_access_key_id=creds.get("s3.access-key-id"),
        aws_secret_access_key=creds.get("s3.secret-access-key"),
        aws_session_token=creds.get("s3.session-token"),
        **client_kwargs,
    )
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    return int(resp.get("KeyCount", 0))


def _list_adls(location: str, creds: dict) -> int:
    from azure.storage.blob import ContainerClient

    parsed = urlparse(location)  # abfss://container@account.dfs.core.windows.net/path
    container, _, host = parsed.netloc.partition("@")
    prefix = parsed.path.lstrip("/")
    # Polaris keys the vended SAS by storage host, e.g. adls.sas-token.<account>.dfs…
    # The trailing dot matters: Iceberg vends the expiry alongside the token as
    # adls.sas-token-expires-at-ms.<account>, and matching without it can select that
    # epoch milliseconds value instead. The SDK then treats the digits as an account
    # key and fails to base64-decode them, which reads as an auth error rather than a
    # wrong-property one.
    sas = next((v for k, v in creds.items() if k.startswith("adls.sas-token.")), None)
    if sas is None:
        raise ValueError("Polaris vended no ADLS SAS token")
    account_url = f"https://{host.replace('.dfs.', '.blob.')}"
    container_client = ContainerClient(
        account_url=account_url, container_name=container, credential=sas
    )
    return sum(1 for _ in container_client.list_blobs(name_starts_with=prefix))
