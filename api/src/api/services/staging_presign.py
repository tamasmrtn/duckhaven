"""Presigned staging URLs for a SQL session's stage (issue #160).

Treats a session's ``staging_uri`` as a Snowflake-style stage: given file names,
mint a short-lived presigned ``PUT`` (upload) and ``GET`` (read) URL per file,
scoped to a key under the session's staging prefix. The client uploads with a
plain HTTP ``PUT``; the agent reads with ``read_parquet('<get_url>')`` over
httpfs. All backend-specific logic lives here (the API already owns storage
config), so both client and agent stay backend-agnostic and speak opaque HTTPS.

Per backend kind:

- ``object_store`` (bundled MinIO): SigV4 presigned URLs with the static MinIO
  credentials the API is configured with. MinIO has no STS, so a presigned URL
  (single key, time-boxed) is the only genuinely *scoped* access available — and
  narrower than the broad static creds Polaris otherwise vends there. Because
  SigV4 signs the host+port, the ``put_url`` is signed for the client-facing
  endpoint and the ``get_url`` for the agent-facing internal endpoint (the agent
  is the sole GET consumer).
- ``s3`` (external): the API assumes the backend's role (boto3 STS) and presigns
  with the returned short-lived credentials. One endpoint, so no put/get split.
- ``adls_gen2`` (external): a user-delegation SAS (AAD identity) per blob — the
  Azure equivalent of a presigned URL.

The cloud SDKs (boto3 / azure-*) are imported lazily so they load only when a
presign actually runs, mirroring ``storage_health`` / ``migration/storage_io``.
SDK calls are synchronous; callers run them off the event loop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from api.config import settings
from api.models import Catalog
from api.services.session_credentials import staging_uri_for


@dataclass
class StagedFile:
    """One staged file's assigned key plus its presigned upload/read URLs."""

    name: str
    key: str
    put_url: str
    get_url: str


class StagingUnavailable(RuntimeError):
    """The session's active backend has no usable staging location."""


def presign_staging_files(
    catalog: Catalog, session_id: uuid.UUID, names: list[str], *, ttl_s: float
) -> tuple[list[StagedFile], datetime]:
    """Presign a ``PUT`` and ``GET`` URL for each name under the session's stage.

    Raises ``StagingUnavailable`` when the backend resolves no staging location.
    """
    staging_uri = staging_uri_for(catalog, session_id)
    if staging_uri is None:
        raise StagingUnavailable("session backend has no staging location")
    kind = catalog.storage_backend.kind
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=ttl_s)
    if kind in ("object_store", "s3"):
        files = _presign_s3(kind, catalog, staging_uri, names, int(ttl_s), session_id)
    elif kind == "adls_gen2":
        files = _presign_adls(staging_uri, names, expires_at)
    else:
        raise StagingUnavailable(f"unsupported backend kind for staging: {kind}")
    return files, expires_at


def staging_read_prefixes(catalog: Catalog, session_id: uuid.UUID) -> list[str]:
    """The HTTPS-form prefix(es) the statement policy admits for
    ``read_parquet('https://…')`` of this session's staged files.

    Built by the *same* URL logic as ``get_url`` (agent-facing internal endpoint
    for object_store; path-style ``<endpoint>/<bucket>/<key-prefix>/`` for S3;
    the blob URL for ADLS) so presign and policy never drift. A get_url starts
    with exactly one of these prefixes.
    """
    staging_uri = staging_uri_for(catalog, session_id)
    if staging_uri is None:
        return []
    kind = catalog.storage_backend.kind
    if kind in ("object_store", "s3"):
        endpoint = _s3_get_endpoint(kind, catalog.storage_backend.config)
        bucket, key_prefix = _s3_bucket_key(staging_uri)
        return [f"{endpoint.rstrip('/')}/{bucket}/{key_prefix}"]
    if kind == "adls_gen2":
        account_url, container, blob_prefix = _adls_parts(staging_uri)
        return [f"{account_url}/{container}/{blob_prefix}"]
    return []


# ── S3 / MinIO ────────────────────────────────────────────────────────────────


def _s3_bucket_key(uri: str) -> tuple[str, str]:
    """``s3://bucket/a/b/`` -> ``("bucket", "a/b/")``."""
    parsed = urlparse(uri)
    return parsed.netloc, parsed.path.lstrip("/")


def _s3_get_endpoint(kind: str, config: dict | None) -> str:
    """The endpoint the agent's httpfs GET reaches (also used for the policy
    prefix). object_store uses the in-network internal endpoint; external s3 uses
    its configured endpoint or the regional AWS host."""
    if kind == "object_store":
        return settings.s3_endpoint_internal
    if config and config.get("endpoint"):
        return config["endpoint"]
    region = (config or {}).get("region") or settings.s3_region
    return f"https://s3.{region}.amazonaws.com"


def _s3_client(endpoint: str, region: str, creds: dict[str, str]):  # noqa: ANN202 - boto3 untyped
    import boto3
    from botocore.config import Config

    # Force path-style so the presigned URL host is deterministically the endpoint
    # host (MinIO requires it anyway); this lets the statement policy compute a
    # matching prefix without guessing virtual-host form.
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        **creds,
    )


def _s3_creds(kind: str, config: dict | None, session_id: uuid.UUID) -> tuple[dict, str]:
    """(boto3 credential kwargs, region) for presigning this backend."""
    if kind == "object_store":
        return (
            {
                "aws_access_key_id": settings.s3_access_key,
                "aws_secret_access_key": settings.s3_secret_key,
            },
            settings.s3_region,
        )
    # External s3: assume the backend's role for short-lived scoped credentials.
    # Base creds come from boto3's default chain (env / instance profile); an
    # AWS_ENDPOINT_URL_STS override is honoured automatically.
    import boto3

    config = config or {}
    assume_kwargs = {
        "RoleArn": config["role_arn"],
        "RoleSessionName": f"dh-staging-{session_id.hex[:24]}",
    }
    if config.get("external_id"):
        assume_kwargs["ExternalId"] = config["external_id"]
    resp = boto3.client("sts").assume_role(**assume_kwargs)
    c = resp["Credentials"]
    return (
        {
            "aws_access_key_id": c["AccessKeyId"],
            "aws_secret_access_key": c["SecretAccessKey"],
            "aws_session_token": c["SessionToken"],
        },
        config.get("region") or settings.s3_region,
    )


def _presign_s3(
    kind: str,
    catalog: Catalog,
    staging_uri: str,
    names: list[str],
    ttl_s: int,
    session_id: uuid.UUID,
) -> list[StagedFile]:
    config = catalog.storage_backend.config
    bucket, key_prefix = _s3_bucket_key(staging_uri)
    creds, region = _s3_creds(kind, config, session_id)
    get_endpoint = _s3_get_endpoint(kind, config)
    # object_store: the client PUTs to the external endpoint, the agent GETs the
    # internal one, and SigV4 binds each URL to its host — so sign each leg with
    # its own client. External s3 has a single endpoint for both.
    put_endpoint = settings.s3_endpoint if kind == "object_store" else get_endpoint
    put_client = _s3_client(put_endpoint, region, creds)
    get_client = (
        put_client if put_endpoint == get_endpoint else _s3_client(get_endpoint, region, creds)
    )

    files: list[StagedFile] = []
    for name in names:
        key = f"{key_prefix}{name}"
        params = {"Bucket": bucket, "Key": key}
        files.append(
            StagedFile(
                name=name,
                key=f"{staging_uri}{name}",
                put_url=put_client.generate_presigned_url(
                    "put_object", Params=params, ExpiresIn=ttl_s
                ),
                get_url=get_client.generate_presigned_url(
                    "get_object", Params=params, ExpiresIn=ttl_s
                ),
            )
        )
    return files


# ── Azure ADLS / Blob ─────────────────────────────────────────────────────────


def _adls_parts(staging_uri: str) -> tuple[str, str, str]:
    """``abfss://container@account.dfs.core.windows.net/p/`` ->
    ``("https://account.blob.core.windows.net", "container", "p/")``."""
    parsed = urlparse(staging_uri)
    container, _, host = parsed.netloc.partition("@")
    account_url = f"https://{host.replace('.dfs.', '.blob.')}"
    return account_url, container, parsed.path.lstrip("/")


def _presign_adls(staging_uri: str, names: list[str], expires_at: datetime) -> list[StagedFile]:
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas

    account_url, container, blob_prefix = _adls_parts(staging_uri)
    account = urlparse(account_url).netloc.split(".", 1)[0]
    start = datetime.now(tz=UTC) - timedelta(minutes=1)  # clock-skew slack
    service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
    delegation_key = service.get_user_delegation_key(
        key_start_time=start, key_expiry_time=expires_at
    )

    def _sas(blob_name: str, *, write: bool) -> str:
        return generate_blob_sas(
            account_name=account,
            container_name=container,
            blob_name=blob_name,
            user_delegation_key=delegation_key,
            permission=BlobSasPermissions(read=True, write=write, create=write),
            start=start,
            expiry=expires_at,
        )

    files: list[StagedFile] = []
    for name in names:
        blob_name = f"{blob_prefix}{name}"
        base = f"{account_url}/{container}/{blob_name}"
        files.append(
            StagedFile(
                name=name,
                key=f"{staging_uri}{name}",
                put_url=f"{base}?{_sas(blob_name, write=True)}",
                get_url=f"{base}?{_sas(blob_name, write=False)}",
            )
        )
    return files
