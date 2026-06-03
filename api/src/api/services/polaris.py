"""Async Apache Polaris (Iceberg REST catalog) client.

Built directly on `httpx`. Polaris exposes two APIs we use:

- the Iceberg REST catalog at ``/api/catalog/v1`` (namespaces + tables),
  prefixed per-catalog (the catalog name is the REST ``prefix``), and
- the Polaris management API at ``/api/management/v1`` (catalogs).

Auth is OAuth2 client-credentials against ``/api/catalog/v1/oauth/tokens``;
the access token is cached and refreshed shortly before expiry.

Errors are raised as `PolarisError` subclasses; success returns a typed
pydantic model. Callers compose idempotency via `PolarisConflictError`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Literal, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# Scope requested for the service-principal token; PRINCIPAL_ROLE:ALL is the
# Polaris convention for "all roles assigned to this principal".
_TOKEN_SCOPE = "PRINCIPAL_ROLE:ALL"
# Refresh the cached token once its remaining lifetime drops below this.
_TOKEN_REFRESH_MARGIN_S = 60.0


# --- Exceptions ---


class PolarisError(Exception):
    """Base for all Polaris client errors."""


class PolarisBadRequestError(PolarisError):
    pass


class PolarisNotFoundError(PolarisError):
    pass


class PolarisConflictError(PolarisError):
    pass


class PolarisServerError(PolarisError):
    pass


# --- Response models ---


class _PolarisModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class PolarisColumn(_PolarisModel):
    name: str
    # Iceberg type string, e.g. "int", "long", "decimal(10,2)".
    type_text: str
    # Upper-cased base type for display parity with the old UC shape.
    type_name: str
    position: int
    nullable: bool = True


class PolarisCatalog(_PolarisModel):
    name: str


class PolarisSchema(_PolarisModel):
    name: str
    catalog_name: str


class PolarisTable(_PolarisModel):
    name: str
    catalog_name: str
    schema_name: str
    table_id: str | None = None
    table_type: Literal["MANAGED", "EXTERNAL"] = "MANAGED"
    data_source_format: str = "ICEBERG"
    storage_location: str | None = None
    columns: list[PolarisColumn] = Field(default_factory=list)
    properties: dict[str, str] | None = None
    comment: str | None = None


# Iceberg primitive type string -> upper-cased display base.
def _iceberg_type_name(type_text: str) -> str:
    base = type_text.split("(", 1)[0]
    return base.upper()


def _columns_from_iceberg_schema(schema: dict[str, Any]) -> list[PolarisColumn]:
    """Map an Iceberg table schema's fields to display columns."""
    cols: list[PolarisColumn] = []
    for position, field in enumerate(schema.get("fields") or []):
        type_text = field.get("type")
        # Only flat/primitive types are produced by DuckHaven's create-table
        # dialog; nested types (struct/list/map) arrive as dicts — stringify.
        if not isinstance(type_text, str):
            type_text = str(type_text)
        cols.append(
            PolarisColumn(
                name=field["name"],
                type_text=type_text,
                type_name=_iceberg_type_name(type_text),
                position=position,
                nullable=not field.get("required", False),
            )
        )
    return cols


# --- Client ---


class PolarisClient:
    """Async Polaris (Iceberg REST + management) client.

    Holds a single `httpx.AsyncClient` whose connection pool is reused
    across the api process. Close via `aclose()` on shutdown.
    """

    CATALOG_PATH = "/api/catalog/v1"
    MGMT_PATH = "/api/management/v1"

    def __init__(
        self,
        base_url: str,
        *,
        realm: str,
        client_id: str,
        client_secret: str,
        principal: str | None = None,
        timeout_s: float = 10.0,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._realm = realm
        self._client_id = client_id
        self._client_secret = client_secret
        # The Polaris principal these credentials authenticate as; used as the
        # grantee when wiring catalog data access. Defaults to the client id
        # (true for the bootstrap `root` principal).
        self._principal = principal or client_id
        self._http = http or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            headers={"Polaris-Realm": realm},
        )
        self._token: str | None = None
        self._token_expiry: float = 0.0
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- Auth ---

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token is not None and time.monotonic() < self._token_expiry:
                return self._token
            resp = await self._http.post(
                f"{self.CATALOG_PATH}/oauth/tokens",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": _TOKEN_SCOPE,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            self._raise_for_status(resp)
            body = resp.json()
            self._token = body["access_token"]
            expires_in = float(body.get("expires_in", 3600))
            self._token_expiry = time.monotonic() + max(0.0, expires_in - _TOKEN_REFRESH_MARGIN_S)
            return self._token

    async def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._access_token()}"}

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        try:
            body = resp.json()
            detail = (body.get("error") or {}).get("message") or body.get("message") or resp.text
        except Exception:  # noqa: BLE001 - any decode error -> use raw body
            detail = resp.text
        if resp.status_code == 404:
            raise PolarisNotFoundError(detail)
        if resp.status_code == 409:
            raise PolarisConflictError(detail)
        if 400 <= resp.status_code < 500:
            raise PolarisBadRequestError(detail)
        raise PolarisServerError(detail)

    # --- Catalogs (management API) ---

    async def create_catalog(
        self,
        name: str,
        *,
        storage_type: str,
        base_location: str,
        allowed_locations: list[str] | None = None,
        extra_storage: dict[str, Any] | None = None,
    ) -> PolarisCatalog:
        """Create an INTERNAL catalog bound to a storage config.

        `storage_type` is the Polaris value (FILE / S3 / GCS / AZURE);
        `base_location` is the catalog's default base location (a file:// or
        cloud URI). `extra_storage` carries backend-specific keys (region,
        roleArn, tenantId, …) merged into `storageConfigInfo`.
        """
        storage_config: dict[str, Any] = {
            "storageType": storage_type,
            "allowedLocations": allowed_locations or [base_location],
            **(extra_storage or {}),
        }
        body = {
            "catalog": {
                "name": name,
                "type": "INTERNAL",
                "readOnly": False,
                "properties": {
                    "default-base-location": base_location,
                    # DuckHaven owns its catalogs: allow DROP ... PURGE so dropping
                    # a table reclaims its data files (default-off feature flag).
                    "polaris.config.drop-with-purge.enabled": "true",
                },
                "storageConfigInfo": storage_config,
            }
        }
        resp = await self._http.post(
            f"{self.MGMT_PATH}/catalogs", json=body, headers=await self._auth_headers()
        )
        self._raise_for_status(resp)
        return PolarisCatalog(name=name)

    async def get_catalog(self, name: str) -> PolarisCatalog:
        resp = await self._http.get(
            f"{self.MGMT_PATH}/catalogs/{name}", headers=await self._auth_headers()
        )
        self._raise_for_status(resp)
        body = resp.json()
        # Management API returns the catalog object directly.
        return PolarisCatalog(name=body.get("name", name))

    async def catalog_exists(self, name: str) -> bool:
        try:
            await self.get_catalog(name)
            return True
        except PolarisNotFoundError:
            return False

    async def delete_catalog(self, name: str) -> None:
        resp = await self._http.delete(
            f"{self.MGMT_PATH}/catalogs/{name}", headers=await self._auth_headers()
        )
        self._raise_for_status(resp)

    # --- Access / grants ---

    _RW_CATALOG_ROLE = "duckhaven_rw"
    _PRINCIPAL_ROLE = "duckhaven"

    # Catalog-level privileges granted to the RW role so the service principal
    # fully owns each DuckHaven catalog: manage content (tables/namespaces +
    # data), metadata, and access (grants).
    _CATALOG_PRIVILEGES = (
        "CATALOG_MANAGE_CONTENT",
        "CATALOG_MANAGE_METADATA",
        "CATALOG_MANAGE_ACCESS",
    )

    async def ensure_catalog_access(self, catalog: str) -> None:
        """Grant the service principal full ownership of a catalog so the agent's
        DuckDB can read, write, and run DDL against its tables.

        Wires the full catalog-management privilege set to a catalog role, binds
        it through a principal role to the service principal. Idempotent:
        re-running tolerates already-existing roles. Without this, Polaris returns
        403 on loadTable even though catalog/table creation succeeds.
        """
        headers = await self._auth_headers()
        role, prole = self._RW_CATALOG_ROLE, self._PRINCIPAL_ROLE

        async def _create(path: str, body: dict[str, Any]) -> None:
            try:
                self._raise_for_status(
                    await self._http.post(f"{self.MGMT_PATH}{path}", json=body, headers=headers)
                )
            except PolarisConflictError:
                pass

        async def _put(path: str, body: dict[str, Any]) -> None:
            try:
                self._raise_for_status(
                    await self._http.put(f"{self.MGMT_PATH}{path}", json=body, headers=headers)
                )
            except PolarisServerError as exc:
                # Polaris's grant-record writes — both privilege grants and the
                # catalog-role/principal-role bindings below — are not idempotent:
                # re-applying an existing one returns 500 with a Postgres duplicate-key
                # body, not a 409. Treat that as already-applied so re-runs are no-ops.
                if "duplicate key" not in str(exc):
                    raise

        await _create(f"/catalogs/{catalog}/catalog-roles", {"catalogRole": {"name": role}})
        for privilege in self._CATALOG_PRIVILEGES:
            await _put(
                f"/catalogs/{catalog}/catalog-roles/{role}/grants",
                {"grant": {"type": "catalog", "privilege": privilege}},
            )
        await _create("/principal-roles", {"principalRole": {"name": prole}})
        await _put(
            f"/principal-roles/{prole}/catalog-roles/{catalog}", {"catalogRole": {"name": role}}
        )
        await _put(
            f"/principals/{self._principal}/principal-roles", {"principalRole": {"name": prole}}
        )

    # --- Namespaces (Iceberg REST) ---

    async def create_schema(self, catalog: str, name: str) -> PolarisSchema:
        resp = await self._http.post(
            f"{self.CATALOG_PATH}/{catalog}/namespaces",
            json={"namespace": [name], "properties": {}},
            headers=await self._auth_headers(),
        )
        self._raise_for_status(resp)
        return PolarisSchema(name=name, catalog_name=catalog)

    async def delete_schema(self, catalog: str, name: str) -> None:
        resp = await self._http.delete(
            f"{self.CATALOG_PATH}/{catalog}/namespaces/{name}",
            headers=await self._auth_headers(),
        )
        self._raise_for_status(resp)

    async def list_schemas(self, catalog: str) -> list[PolarisSchema]:
        resp = await self._http.get(
            f"{self.CATALOG_PATH}/{catalog}/namespaces", headers=await self._auth_headers()
        )
        self._raise_for_status(resp)
        namespaces = resp.json().get("namespaces") or []
        # Each namespace is a list of levels; DuckHaven uses single-level names.
        return [
            PolarisSchema(name=ns[0], catalog_name=catalog)
            for ns in namespaces
            if isinstance(ns, list) and ns
        ]

    # --- Tables (Iceberg REST) ---

    async def list_tables(self, catalog: str, schema: str) -> list[PolarisTable]:
        """List tables in a namespace. Iceberg REST returns identifiers only
        (no schema), so columns are empty here — use `get_table` for detail."""
        resp = await self._http.get(
            f"{self.CATALOG_PATH}/{catalog}/namespaces/{schema}/tables",
            headers=await self._auth_headers(),
        )
        self._raise_for_status(resp)
        identifiers = resp.json().get("identifiers") or []
        return [
            PolarisTable(name=ident["name"], catalog_name=catalog, schema_name=schema)
            for ident in identifiers
        ]

    async def get_table(self, catalog: str, schema: str, name: str) -> PolarisTable:
        resp = await self._http.get(
            f"{self.CATALOG_PATH}/{catalog}/namespaces/{schema}/tables/{name}",
            headers=await self._auth_headers(),
        )
        self._raise_for_status(resp)
        return self._table_from_load_result(resp.json(), catalog, schema, name)

    async def create_table(
        self,
        *,
        catalog: str,
        schema: str,
        name: str,
        columns: list[dict[str, Any]],
        comment: str | None = None,
    ) -> PolarisTable:
        """Create an Iceberg table. `columns` are Iceberg schema fields
        (`{id, name, required, type}`); Polaris writes the initial metadata
        and places the table under the catalog's base location."""
        body: dict[str, Any] = {
            "name": name,
            "schema": {"type": "struct", "schema-id": 0, "fields": columns},
        }
        if comment:
            body["properties"] = {"comment": comment}
        resp = await self._http.post(
            f"{self.CATALOG_PATH}/{catalog}/namespaces/{schema}/tables",
            json=body,
            headers=await self._auth_headers(),
        )
        self._raise_for_status(resp)
        return self._table_from_load_result(resp.json(), catalog, schema, name)

    async def delete_table(
        self, catalog: str, schema: str, name: str, *, purge: bool = False
    ) -> None:
        # Default is the Iceberg REST behaviour (drop metadata only). DuckHaven's
        # own drop paths pass purge=True against fully-owned catalogs, which
        # enable DROP_WITH_PURGE_ENABLED and grant CATALOG_MANAGE_CONTENT
        # (i.e. TABLE_WRITE_DATA) so purge is authorized and reclaims data files.
        resp = await self._http.delete(
            f"{self.CATALOG_PATH}/{catalog}/namespaces/{schema}/tables/{name}",
            params={"purgeRequested": "true"} if purge else None,
            headers=await self._auth_headers(),
        )
        self._raise_for_status(resp)

    @staticmethod
    def _table_from_load_result(
        body: dict[str, Any], catalog: str, schema: str, name: str
    ) -> PolarisTable:
        """Map an Iceberg REST LoadTableResult to a PolarisTable."""
        metadata = body.get("metadata") or {}
        # Resolve the current schema from schemas[]/current-schema-id, falling
        # back to a top-level "schema" if present.
        current_id = metadata.get("current-schema-id")
        table_schema: dict[str, Any] = {}
        for s in metadata.get("schemas") or []:
            if s.get("schema-id") == current_id:
                table_schema = s
                break
        if not table_schema:
            table_schema = metadata.get("schema") or (metadata.get("schemas") or [{}])[0]
        return PolarisTable(
            name=name,
            catalog_name=catalog,
            schema_name=schema,
            table_id=metadata.get("table-uuid"),
            storage_location=body.get("metadata-location") or metadata.get("location"),
            columns=_columns_from_iceberg_schema(table_schema),
            properties=metadata.get("properties") or {},
        )
