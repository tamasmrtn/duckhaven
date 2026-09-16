"""Per-session credential vending (the seam).

This centralizes what a session's held DuckDB connection uses to reach Polaris and
where a load may stage bulk Parquet. Today it vends the API's single shared Polaris
service principal — the same identity the agent used from its own config — but it
moves the **vend point to the API**, which is the hook a future per-principal
Polaris identity (and real STS-scoped staging credentials) plugs into. Governance
today rests on the API's per-statement authorization (``grants.assert_query_access``)
plus the ``catalog_grants`` ACL, not on the Polaris token's identity.

It is also where DuckLake's credentials come from, and there it is not just a
hook but the whole mechanism. An Iceberg catalog gets its storage credentials
from Polaris at ATTACH time, so the control plane vends nothing. DuckLake has no
credential vendor: the agent connects to the catalog database and to object
storage itself. Both credentials are therefore minted here and travel in the
dispatch payload — never written to the agent's config or disk, which is the
closest this design gets to preserving I7.

Deferred (env-gated integration tests): minting a distinct Polaris principal per
DuckHaven principal, and true short-lived STS credentials for the staging prefix
(the bundled backend has no STS — staging scoping there is the unique prefix
plus the statement policy that a ``COPY`` may only touch it).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from api.config import settings
from api.models import Catalog
from api.models.storage_backend import StorageBackend
from api.services.workspace import polaris_storage

# How long a vended DuckLake storage credential is good for. Long enough that a
# slow query does not lose its credential mid-scan, short enough that a leaked
# one expires. Only meaningful for the kinds that can mint a scoped credential
# (external s3 via STS, ADLS via a user-delegation SAS); the bundled store has
# no such mechanism — see `build_storage_block`.
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

    Resolved through ``polaris_storage`` for the same reason ``staging_uri_for``
    does — the bundled backend's ``root_uri`` is a bucket-relative prefix label,
    not a URI — and scoped per catalog exactly as ``ensure_polaris_catalog``
    scopes an Iceberg catalog's base location, so two catalogs sharing a backend
    never collide.
    """
    backend = catalog.storage_backend
    _, base, _ = polaris_storage(backend.kind, backend.root_uri or "", backend.config)
    base = base.rstrip("/")
    if not base:
        raise ValueError(f"Storage backend for catalog {catalog.slug} has no base location")
    return f"{base}/{catalog.slug}/"


def build_ducklake_meta_block() -> dict[str, str | int]:
    """Postgres connection details an agent needs to reach a DuckLake catalog.

    The restricted ``ducklake_agent`` role, which can open the ``ducklake``
    database and nothing else. Sent per dispatch rather than configured on the
    agent so it is never written to agent disk, and so rotating it does not
    require touching every agent.
    """
    return {
        "host": settings.ducklake_agent_host,
        "port": settings.ducklake_agent_port,
        "database": settings.ducklake_agent_database,
        "user": settings.ducklake_agent_user,
        "password": settings.ducklake_agent_password,
    }


def build_storage_block(backend: StorageBackend, data_path: str) -> dict[str, object]:
    """Storage credentials for a DuckLake catalog's data path.

    What Polaris vends for an Iceberg catalog, minted by DuckHaven instead. The
    three backends differ in how good the credential is, and the difference is
    worth being explicit about:

    - ``s3`` — a real STS ``AssumeRole``, expiring within the hour. I7 fully
      preserved, via the same helper the staging presigner already uses.
    - ``adls_gen2`` — a user-delegation SAS over the catalog's container,
      expiring within the hour. I7 preserved.
    - ``object_store`` — the bundled store's static key. **This is weaker than
      what Polaris vends today**, which is a short-lived STS credential even for
      the bundled store. What bounds it is ``SCOPE``: the secret is usable only
      for this catalog's prefix, and it dies with the connection. Closing this
      gap needs the bundled store's own STS endpoint driven from the API, which
      is not wired up.
    """
    if backend.kind == "adls_gen2":
        return _adls_storage_block(data_path)
    return _s3_storage_block(backend, data_path)


def _s3_storage_block(backend: StorageBackend, data_path: str) -> dict[str, object]:
    config = backend.config or {}
    block: dict[str, object] = {
        "type": "s3",
        # Scope the secret to this catalog's prefix, so one catalog's credential
        # cannot serve another's data even though both are attached to the same
        # connection.
        "scope": data_path,
        "url_style": "path" if backend.kind == "object_store" else "vhost",
    }
    if backend.kind == "object_store":
        endpoint = settings.s3_endpoint
        parsed = urlparse(endpoint)
        block.update(
            {
                "key_id": settings.s3_access_key,
                "secret": settings.s3_secret_key,
                "session_token": "",
                "region": settings.s3_region,
                # DuckDB wants host[:port], not a scheme.
                "endpoint": parsed.netloc or endpoint,
                "use_ssl": parsed.scheme == "https",
            }
        )
        return block

    # External S3: assume the backend's role for short-lived scoped credentials.
    import boto3

    assume_kwargs: dict[str, object] = {
        "RoleArn": config["role_arn"],
        "RoleSessionName": f"dh-ducklake-{datetime.now(tz=UTC):%Y%m%d%H%M%S}",
        "DurationSeconds": int(DUCKLAKE_CREDENTIAL_TTL.total_seconds()),
    }
    if config.get("external_id"):
        assume_kwargs["ExternalId"] = config["external_id"]
    creds = boto3.client("sts").assume_role(**assume_kwargs)["Credentials"]
    block.update(
        {
            "key_id": creds["AccessKeyId"],
            "secret": creds["SecretAccessKey"],
            "session_token": creds["SessionToken"],
            "region": config.get("region") or settings.s3_region,
            "endpoint": config.get("endpoint") or "",
            "use_ssl": True,
        }
    )
    if config.get("path_style_access"):
        block["url_style"] = "path"
    return block


def _adls_storage_block(data_path: str) -> dict[str, object]:
    """A container-scoped user-delegation SAS for the catalog's data path.

    Container-level rather than the per-blob SAS the staging presigner mints:
    an agent writing a DuckLake table creates blob names DuckHaven has never
    seen, so there is nothing to enumerate up front.
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
