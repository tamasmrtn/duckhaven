"""Provision real Apache Polaris (Iceberg REST) catalogs for tests.

Built on raw ``httpx`` with no dependency on the ``api`` or ``agent`` packages,
so any suite can stand up an S3-backed catalog with the RBAC grants DuckHaven's
agent needs to read, write, and run DDL. Mirrors what
``api.services.polaris.PolarisClient.create_catalog`` +
``ensure_catalog_access`` do in production, plus a seeded ``analytics.events``
table so DuckDB attach paths have something to read immediately.

Polaris is object-storage only (see ADR 0001); every catalog is S3-backed and
requires ``POLARIS_S3_BUCKET`` (+ ``POLARIS_S3_ENDPOINT[_INTERNAL]``).
``make polaris-dev`` provides a local MinIO-backed stack.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx

CATALOG_API = "/api/catalog/v1"
MGMT_API = "/api/management/v1"
REALM = "POLARIS"
DEFAULT_NAMESPACE = "analytics"

# Full catalog ownership for the service principal: manage content
# (tables/namespaces + data), metadata, and access (grants). Matches
# PolarisClient._CATALOG_PRIVILEGES.
_CATALOG_PRIVILEGES = (
    "CATALOG_MANAGE_CONTENT",
    "CATALOG_MANAGE_METADATA",
    "CATALOG_MANAGE_ACCESS",
)
_RW_CATALOG_ROLE = "duckhaven_rw"
_PRINCIPAL_ROLE = "duckhaven"


def health_url(base_url: str) -> str:
    """Polaris health/metrics live on the management port (8182); API on 8181."""
    return base_url.replace(":8181", ":8182").rstrip("/") + "/q/health"


def env_creds() -> tuple[str, str]:
    """(client_id, client_secret) from env, defaulting to the bootstrap root principal."""
    return os.getenv("POLARIS_CLIENT_ID", "root"), os.getenv("POLARIS_CLIENT_SECRET", "s3cr3t")


def s3_storage_config(base_location: str) -> dict[str, Any]:
    """Build an S3 ``storageConfigInfo`` from ``POLARIS_S3_*`` (bundled MinIO)."""
    storage: dict[str, Any] = {
        "storageType": "S3",
        "allowedLocations": [base_location],
        "region": os.getenv("POLARIS_S3_REGION", "us-east-1"),
    }
    if endpoint := os.getenv("POLARIS_S3_ENDPOINT"):
        storage["endpoint"] = endpoint
        storage["pathStyleAccess"] = True
    if internal := os.getenv("POLARIS_S3_ENDPOINT_INTERNAL"):
        storage["endpointInternal"] = internal
    return storage


async def access_token(client: httpx.AsyncClient, creds: tuple[str, str]) -> str:
    """OAuth2 client-credentials exchange against the Polaris token endpoint."""
    resp = await client.post(
        f"{CATALOG_API}/oauth/tokens",
        data={
            "grant_type": "client_credentials",
            "client_id": creds[0],
            "client_secret": creds[1],
            "scope": "PRINCIPAL_ROLE:ALL",
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


async def provision_catalog(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    name: str,
    base_location: str,
    storage_config: dict[str, Any],
    principal: str,
    *,
    seed_table: bool = True,
) -> None:
    """Create an INTERNAL S3 catalog with full RBAC grants for the principal.

    When ``seed_table`` is set, also creates an ``analytics`` namespace holding
    an ``events(id long, label string)`` table (mirrors PolarisClient +
    ensure_catalog_access so the agent can attach and read straight away).
    """
    await client.post(
        f"{MGMT_API}/catalogs",
        headers=headers,
        json={
            "catalog": {
                "name": name,
                "type": "INTERNAL",
                "readOnly": False,
                "properties": {
                    "default-base-location": base_location,
                    "polaris.config.drop-with-purge.enabled": "true",
                },
                "storageConfigInfo": storage_config,
            }
        },
    )
    await client.post(
        f"{MGMT_API}/catalogs/{name}/catalog-roles",
        headers=headers,
        json={"catalogRole": {"name": _RW_CATALOG_ROLE}},
    )
    for privilege in _CATALOG_PRIVILEGES:
        await client.put(
            f"{MGMT_API}/catalogs/{name}/catalog-roles/{_RW_CATALOG_ROLE}/grants",
            headers=headers,
            json={"grant": {"type": "catalog", "privilege": privilege}},
        )
    await client.post(
        f"{MGMT_API}/principal-roles",
        headers=headers,
        json={"principalRole": {"name": _PRINCIPAL_ROLE}},
    )
    await client.put(
        f"{MGMT_API}/principal-roles/{_PRINCIPAL_ROLE}/catalog-roles/{name}",
        headers=headers,
        json={"catalogRole": {"name": _RW_CATALOG_ROLE}},
    )
    await client.put(
        f"{MGMT_API}/principals/{principal}/principal-roles",
        headers=headers,
        json={"principalRole": {"name": _PRINCIPAL_ROLE}},
    )
    if not seed_table:
        return
    await client.post(
        f"{CATALOG_API}/{name}/namespaces",
        headers=headers,
        json={"namespace": [DEFAULT_NAMESPACE]},
    )
    await client.post(
        f"{CATALOG_API}/{name}/namespaces/{DEFAULT_NAMESPACE}/tables",
        headers=headers,
        json={
            "name": "events",
            "schema": {
                "type": "struct",
                "schema-id": 0,
                "fields": [
                    {"id": 1, "name": "id", "required": False, "type": "long"},
                    {"id": 2, "name": "label", "required": False, "type": "string"},
                ],
            },
        },
    )


async def delete_catalog(client: httpx.AsyncClient, headers: dict[str, str], name: str) -> None:
    """Best-effort catalog teardown (tolerates an already-deleted catalog)."""
    with contextlib.suppress(httpx.HTTPError):
        await client.delete(f"{MGMT_API}/catalogs/{name}", headers=headers)


@contextlib.asynccontextmanager
async def s3_catalog(
    base_url: str,
    creds: tuple[str, str],
    *,
    prefix: str,
    seed_table: bool = True,
) -> AsyncIterator[tuple[str, str]]:
    """Create a uniquely-named S3 catalog, yield ``(catalog_name, namespace)``,
    then tear it down. ``prefix`` namespaces the catalog by suite (e.g. ``dh_agt``).

    Requires ``POLARIS_S3_BUCKET``; the caller is expected to skip when unset.
    """
    bucket = os.environ["POLARIS_S3_BUCKET"].rstrip("/")
    name = f"{prefix}_{uuid4().hex[:10]}"
    base = f"{bucket}/{uuid4().hex[:8]}"
    storage = s3_storage_config(base)
    async with httpx.AsyncClient(base_url=base_url, timeout=15.0) as client:
        headers = {
            "Authorization": f"Bearer {await access_token(client, creds)}",
            "Polaris-Realm": REALM,
        }
        await provision_catalog(
            client, headers, name, base, storage, creds[0], seed_table=seed_table
        )
        try:
            yield name, DEFAULT_NAMESPACE
        finally:
            await delete_catalog(client, headers, name)
