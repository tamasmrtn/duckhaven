"""Backend-agnostic object IO over Polaris-vended credentials.

Migration copies Iceberg files between two storage backends. Both ends speak
object storage; the only differences are the wire protocol (S3 vs ADLS) and the
short-lived credentials Polaris vends. This module wraps list/get/put/exists over
those, keyed by backend kind, reusing the same vended-credential shapes the
storage-health check already relies on.

``object_store`` (bundled MinIO) and ``s3`` both use the S3 path; ``adls_gen2``
uses the Azure Blob path. The cloud SDKs are imported lazily so they load only
when a migration actually runs. SDK calls are synchronous; callers run them off
the event loop via ``asyncio.to_thread``.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

# Backend kind -> the object-IO protocol it speaks.
_S3_KINDS = {"object_store", "s3"}
_ADLS_KINDS = {"adls_gen2"}


@dataclass
class StorageContext:
    """Everything needed to read or write one backend: its protocol plus the
    vended credentials (and the backend's own config as a fallback for the
    endpoint/region that S3-compatible stores omit from vended creds)."""

    kind: str
    creds: dict
    config: dict

    @property
    def proto(self) -> str:
        if self.kind in _S3_KINDS:
            return "s3"
        if self.kind in _ADLS_KINDS:
            return "adls"
        raise ValueError(f"Unsupported backend kind for migration IO: {self.kind}")


def list_objects(ctx: StorageContext, location: str) -> list[tuple[str, int]]:
    """Return ``(absolute_uri, size_bytes)`` for every object under ``location``."""
    if ctx.proto == "s3":
        return _s3_list(ctx, location)
    return _adls_list(ctx, location)


def get_object(ctx: StorageContext, uri: str) -> bytes:
    if ctx.proto == "s3":
        return _s3_get(ctx, uri)
    return _adls_get(ctx, uri)


def put_object(ctx: StorageContext, uri: str, data: bytes) -> None:
    if ctx.proto == "s3":
        _s3_put(ctx, uri, data)
    else:
        _adls_put(ctx, uri, data)


def object_size(ctx: StorageContext, uri: str) -> int | None:
    """Size of ``uri`` if it exists, else ``None`` — used for copy-if-absent."""
    if ctx.proto == "s3":
        return _s3_size(ctx, uri)
    return _adls_size(ctx, uri)


# --- S3 / MinIO ---


def _s3_client(ctx: StorageContext):  # noqa: ANN202 - boto3 client is untyped
    import boto3

    creds, config = ctx.creds, ctx.config
    kwargs: dict[str, str] = {}
    if endpoint := (creds.get("s3.endpoint") or config.get("endpoint")):
        kwargs["endpoint_url"] = endpoint
    if region := (creds.get("client.region") or creds.get("s3.region") or config.get("region")):
        kwargs["region_name"] = region
    return boto3.client(
        "s3",
        aws_access_key_id=creds.get("s3.access-key-id"),
        aws_secret_access_key=creds.get("s3.secret-access-key"),
        aws_session_token=creds.get("s3.session-token"),
        **kwargs,
    )


def _s3_bucket_key(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    return parsed.netloc, parsed.path.lstrip("/")


def _s3_list(ctx: StorageContext, location: str) -> list[tuple[str, int]]:
    client = _s3_client(ctx)
    bucket, prefix = _s3_bucket_key(location)
    # A directory-style trailing slash: Polaris's vended STS credentials scope
    # s3:ListBucket to an `s3:prefix` StringLike condition of `<location>/*`,
    # which only matches a request prefix that itself ends in "/" — without it
    # MinIO denies the call outright. It also stops a bare prefix match from
    # sweeping in a sibling table whose name is a superstring (e.g. "users" vs
    # "users2").
    if not prefix.endswith("/"):
        prefix += "/"
    out: list[tuple[str, int]] = []
    token: str | None = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents") or []:
            out.append((f"s3://{bucket}/{obj['Key']}", int(obj.get("Size", 0))))
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    return out


def _s3_get(ctx: StorageContext, uri: str) -> bytes:
    client = _s3_client(ctx)
    bucket, key = _s3_bucket_key(uri)
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


def _s3_put(ctx: StorageContext, uri: str, data: bytes) -> None:
    client = _s3_client(ctx)
    bucket, key = _s3_bucket_key(uri)
    client.put_object(Bucket=bucket, Key=key, Body=data)


def _s3_size(ctx: StorageContext, uri: str) -> int | None:
    from botocore.exceptions import ClientError

    client = _s3_client(ctx)
    bucket, key = _s3_bucket_key(uri)
    try:
        return int(client.head_object(Bucket=bucket, Key=key)["ContentLength"])
    except ClientError:
        return None


# --- ADLS Gen2 ---


def _adls_container(ctx: StorageContext, uri: str):  # noqa: ANN202 - azure client is untyped
    from azure.storage.blob import ContainerClient

    parsed = urlparse(uri)  # abfss://container@account.dfs.core.windows.net/path
    container, _, host = parsed.netloc.partition("@")
    # Trailing dot required: Iceberg also vends adls.sas-token-expires-at-ms.<account>,
    # which a looser prefix can match instead of the token itself.
    sas = next((v for k, v in ctx.creds.items() if k.startswith("adls.sas-token.")), None)
    if sas is None:
        raise ValueError("Polaris vended no ADLS SAS token")
    account_url = f"https://{host.replace('.dfs.', '.blob.')}"
    return ContainerClient(account_url=account_url, container_name=container, credential=sas)


def _adls_path(uri: str) -> str:
    return urlparse(uri).path.lstrip("/")


def _adls_host(uri: str) -> str:
    """The ``container@host`` authority, to rebuild absolute URIs from blob names."""
    return urlparse(uri).netloc


def _adls_list(ctx: StorageContext, location: str) -> list[tuple[str, int]]:
    client = _adls_container(ctx, location)
    netloc = _adls_host(location)
    prefix = _adls_path(location)
    # Directory-style trailing slash so a bare prefix match can't sweep in a
    # sibling table whose name is a superstring (e.g. "users" vs "users2").
    if not prefix.endswith("/"):
        prefix += "/"
    out: list[tuple[str, int]] = []
    for blob in client.list_blobs(name_starts_with=prefix):
        out.append((f"abfss://{netloc}/{blob.name}", int(blob.size or 0)))
    return out


def _adls_get(ctx: StorageContext, uri: str) -> bytes:
    client = _adls_container(ctx, uri)
    return client.download_blob(_adls_path(uri)).readall()


def _adls_put(ctx: StorageContext, uri: str, data: bytes) -> None:
    client = _adls_container(ctx, uri)
    client.upload_blob(_adls_path(uri), data, overwrite=True)


def _adls_size(ctx: StorageContext, uri: str) -> int | None:
    from azure.core.exceptions import ResourceNotFoundError

    client = _adls_container(ctx, uri)
    try:
        return int(client.get_blob_client(_adls_path(uri)).get_blob_properties().size)
    except ResourceNotFoundError:
        return None
