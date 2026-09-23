"""Backend-agnostic object IO over Polaris-vended credentials.

Migration copies Iceberg files between two storage backends. Both ends speak
object storage; the only differences are the wire protocol (S3 vs ADLS) and the
short-lived credentials Polaris vends. This module wraps list/get/put/exists over
those, keyed by backend kind, reusing the same vended-credential shapes the
storage-health check already relies on.

``object_store`` (the bundled store) and ``s3`` both use the S3 path; ``adls_gen2``
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


# --- S3 ---


def context_from_duckdb_block(kind: str, block: dict, config: dict | None) -> StorageContext:
    """Build a context from `session_credentials.build_storage_block`'s output.

    Translates DuckDB's credential spelling (``key_id``) into the Iceberg REST
    one (``s3.access-key-id``) this module speaks.
    """
    if block.get("type") == "azure":
        # `_adls_container` wants a bare SAS keyed by account.
        conn = str(block.get("connection_string") or "")
        sas = next(
            (
                part.split("=", 1)[1]
                for part in conn.split(";")
                if part.startswith("SharedAccessSignature=")
            ),
            "",
        )
        return StorageContext(
            kind=kind,
            creds={f"adls.sas-token.{block.get('account_name', '')}": sas},
            config=config or {},
        )

    endpoint = str(block.get("endpoint") or "")
    if endpoint and "://" not in endpoint:
        # DuckDB wants a bare host; boto3 wants the scheme back.
        endpoint = f"{'https' if block.get('use_ssl') else 'http'}://{endpoint}"
    creds = {
        "s3.access-key-id": block.get("key_id"),
        "s3.secret-access-key": block.get("secret"),
        "s3.session-token": block.get("session_token") or None,
        "client.region": block.get("region"),
        "s3.endpoint": endpoint or None,
    }
    return StorageContext(kind=kind, creds=creds, config=config or {})


def copy_object(src: StorageContext, dst: StorageContext, src_uri: str, dst_uri: str) -> int:
    """Copy one object, server-side where both ends allow it, else streamed.

    Returns the bytes copied. Never buffers whole objects: DuckLake data files
    default to 512 MB.
    """
    if src.proto == "s3" and dst.proto == "s3" and _same_s3_endpoint(src, dst):
        return _s3_server_side_copy(src, dst, src_uri, dst_uri)
    return _stream_object(src, dst, src_uri, dst_uri)


def delete_prefix(ctx: StorageContext, location: str) -> int:
    """Delete every object under ``location``. Returns the count."""
    if ctx.proto == "s3":
        return _s3_delete_prefix(ctx, location)
    return _adls_delete_prefix(ctx, location)


def _same_s3_endpoint(src: StorageContext, dst: StorageContext) -> bool:
    """Whether one client can see both ends, as a server-side copy requires."""

    def endpoint(ctx: StorageContext) -> str:
        return str(ctx.creds.get("s3.endpoint") or ctx.config.get("endpoint") or "")

    return endpoint(src) == endpoint(dst) and src.creds.get("s3.access-key-id") == dst.creds.get(
        "s3.access-key-id"
    )


def _s3_server_side_copy(
    src: StorageContext, dst: StorageContext, src_uri: str, dst_uri: str
) -> int:
    client = _s3_client(dst)
    src_bucket, src_key = _s3_bucket_key(src_uri)
    dst_bucket, dst_key = _s3_bucket_key(dst_uri)
    client.copy_object(
        Bucket=dst_bucket, Key=dst_key, CopySource={"Bucket": src_bucket, "Key": src_key}
    )
    return _s3_size(dst, dst_uri) or 0


def _stream_object(src: StorageContext, dst: StorageContext, src_uri: str, dst_uri: str) -> int:
    """Stream an object across backends; peak memory is the SDK's part size."""
    if src.proto == "s3":
        body = _s3_client(src).get_object(
            Bucket=_s3_bucket_key(src_uri)[0], Key=_s3_bucket_key(src_uri)[1]
        )["Body"]
    else:
        body = _adls_container(src, src_uri).download_blob(_adls_path(src_uri))
        body = body.chunks()  # type: ignore[assignment]

    if dst.proto == "s3":
        bucket, key = _s3_bucket_key(dst_uri)
        _s3_client(dst).upload_fileobj(body, bucket, key)
    else:
        _adls_container(dst, dst_uri).upload_blob(_adls_path(dst_uri), body, overwrite=True)
    return object_size(dst, dst_uri) or 0


def _s3_delete_prefix(ctx: StorageContext, location: str) -> int:
    client = _s3_client(ctx)
    bucket, prefix = _s3_bucket_key(location)
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            deleted += len(keys)
    return deleted


def _adls_delete_prefix(ctx: StorageContext, location: str) -> int:
    container = _adls_container(ctx, location)
    prefix = _adls_path(location)
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    deleted = 0
    for blob in container.list_blobs(name_starts_with=prefix):
        container.delete_blob(blob.name)
        deleted += 1
    return deleted


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
    # the store denies the call outright. It also stops a bare prefix match from
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
