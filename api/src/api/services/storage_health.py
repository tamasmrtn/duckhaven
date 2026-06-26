"""Validate that an external storage backend's vended credentials reach storage.

The check provisions a throwaway Polaris catalog from the backend's config and
creates a tiny Iceberg table — forcing Polaris to assume the role / consent the
app and write metadata to the operator's storage. It then asks Polaris to vend
short-lived scoped client credentials (the same path agents use) and uses them
to LIST the probe location from the API. Any failure surfaces as
``valid=False`` with a sanitized detail; everything is cleaned up best-effort.

The cloud SDKs (boto3 / azure-storage-blob) are imported lazily so they are only
loaded when a health check actually runs.
"""

from __future__ import annotations

import logging
import uuid
from urllib.parse import urlparse

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
    return " ".join(str(exc).split())[:300]


async def validate_backend(polaris: PolarisClient, backend: StorageBackend) -> StorageBackendHealth:
    """Validate external storage access end to end. object_store always passes."""
    if backend.kind == "object_store":
        return StorageBackendHealth(
            valid=True, detail="Bundled object store; no external credentials to validate."
        )

    storage_type, base_location, extra = polaris_storage(
        backend.kind, backend.root_uri, backend.config
    )
    temp = f"dhhealth{uuid.uuid4().hex[:12]}"
    try:
        await polaris.create_catalog(
            temp, storage_type=storage_type, base_location=base_location, extra_storage=extra
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
        location = (body.get("metadata") or {}).get("location") or base_location
        count = _list_prefix(backend.kind, location, creds)
        return StorageBackendHealth(
            valid=True,
            detail=f"Vended credentials reached storage ({count} object(s) under the probe path).",
        )
    except Exception as exc:  # noqa: BLE001 — any failure means the backend isn't usable
        logger.info("Storage health check failed for backend=%s: %s", backend.id, _short(exc))
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


def _list_prefix(kind: str, location: str, creds: dict) -> int:
    """LIST the probe location with the vended credentials; return object count."""
    if kind == "s3":
        return _list_s3(location, creds)
    if kind == "adls_gen2":
        return _list_adls(location, creds)
    raise ValueError(f"Unsupported backend kind for health check: {kind}")


def _list_s3(location: str, creds: dict) -> int:
    import boto3

    parsed = urlparse(location)
    bucket, prefix = parsed.netloc, parsed.path.lstrip("/")
    client_kwargs: dict[str, str] = {}
    if endpoint := creds.get("s3.endpoint"):
        client_kwargs["endpoint_url"] = endpoint
    if region := (creds.get("client.region") or creds.get("s3.region")):
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
    sas = next((v for k, v in creds.items() if k.startswith("adls.sas-token")), None)
    if sas is None:
        raise ValueError("Polaris vended no ADLS SAS token")
    account_url = f"https://{host.replace('.dfs.', '.blob.')}"
    container_client = ContainerClient(
        account_url=account_url, container_name=container, credential=sas
    )
    return sum(1 for _ in container_client.list_blobs(name_starts_with=prefix))
