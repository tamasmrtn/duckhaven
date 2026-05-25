"""Async Unity Catalog OSS REST client.

Built directly on `httpx`. The published `unitycatalog` Python SDK
(v0.1.1, the only release at the time of writing) is broken against
httpx >= 0.28 (passes the deprecated `proxies` kwarg to httpx.AsyncClient
internally), so this client speaks the REST contract directly. The
endpoints used here are the ones spike S2 / S3 / S5 validated against
the running UC container.

Errors are raised as `UCError` subclasses; success returns a typed
pydantic model. Caller composes idempotency via `UCConflictError`.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


# --- Exceptions ---


class UCError(Exception):
    """Base for all Unity Catalog client errors."""


class UCBadRequestError(UCError):
    pass


class UCNotFoundError(UCError):
    pass


class UCConflictError(UCError):
    pass


class UCServerError(UCError):
    pass


# --- Response models ---


class _UCModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class UCColumn(_UCModel):
    name: str
    type_text: str
    type_name: str
    type_json: str | None = None
    type_precision: int = 0
    type_scale: int = 0
    type_interval_type: str | None = None
    position: int
    nullable: bool = True
    comment: str | None = None


class UCCatalog(_UCModel):
    name: str
    comment: str | None = None
    created_at: int | None = None


class UCSchema(_UCModel):
    name: str
    catalog_name: str
    full_name: str | None = None
    comment: str | None = None


class UCTable(_UCModel):
    name: str
    catalog_name: str
    schema_name: str
    table_id: str | None = None
    table_type: Literal["MANAGED", "EXTERNAL"]
    data_source_format: str
    storage_location: str | None = None
    columns: list[UCColumn] = Field(default_factory=list)
    properties: dict[str, str] | None = None
    comment: str | None = None


class UCTemporaryCredentials(_UCModel):
    aws_temp_credentials: dict[str, Any] | None = None
    azure_user_delegation_sas: dict[str, Any] | None = None
    gcp_oauth_token: dict[str, Any] | None = None
    expiration_time: int | str | None = None


# --- Client ---


class UCClient:
    """Async Unity Catalog REST client.

    Holds a single `httpx.AsyncClient` whose connection pool is reused
    across the api process. Close via `aclose()` on shutdown.
    """

    BASE_PATH = "/api/2.1/unity-catalog"

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout_s: float = 10.0,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = http or httpx.AsyncClient(
            base_url=base_url, timeout=timeout_s, headers=headers
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        try:
            detail = resp.json().get("message") or resp.text
        except Exception:  # noqa: BLE001 - any decode error -> use raw body
            detail = resp.text
        if resp.status_code == 404:
            raise UCNotFoundError(detail)
        if resp.status_code == 409:
            raise UCConflictError(detail)
        if 400 <= resp.status_code < 500:
            lower = (detail or "").lower()
            # UC OSS sometimes returns 400 with "already exists" on duplicates;
            # promote that to UCConflictError so callers can dispatch on it.
            if "exists" in lower or "duplicate" in lower:
                raise UCConflictError(detail)
            raise UCBadRequestError(detail)
        raise UCServerError(detail)

    # --- Catalogs ---

    async def create_catalog(self, name: str, comment: str | None = None) -> UCCatalog:
        resp = await self._http.post(
            f"{self.BASE_PATH}/catalogs",
            json={"name": name, "comment": comment or ""},
        )
        self._raise_for_status(resp)
        return UCCatalog.model_validate(resp.json())

    async def get_catalog(self, name: str) -> UCCatalog:
        resp = await self._http.get(f"{self.BASE_PATH}/catalogs/{name}")
        self._raise_for_status(resp)
        return UCCatalog.model_validate(resp.json())

    async def catalog_exists(self, name: str) -> bool:
        try:
            await self.get_catalog(name)
            return True
        except UCNotFoundError:
            return False

    async def delete_catalog(self, name: str, *, force: bool = True) -> None:
        resp = await self._http.delete(
            f"{self.BASE_PATH}/catalogs/{name}",
            params={"force": "true"} if force else None,
        )
        self._raise_for_status(resp)

    # --- Schemas ---

    async def create_schema(self, catalog: str, name: str, comment: str | None = None) -> UCSchema:
        resp = await self._http.post(
            f"{self.BASE_PATH}/schemas",
            json={"name": name, "catalog_name": catalog, "comment": comment or ""},
        )
        self._raise_for_status(resp)
        return UCSchema.model_validate(resp.json())

    async def list_schemas(self, catalog: str) -> list[UCSchema]:
        resp = await self._http.get(f"{self.BASE_PATH}/schemas", params={"catalog_name": catalog})
        self._raise_for_status(resp)
        body = resp.json().get("schemas") or []
        return [UCSchema.model_validate(s) for s in body]

    # --- Tables ---

    async def list_tables(self, catalog: str, schema: str) -> list[UCTable]:
        resp = await self._http.get(
            f"{self.BASE_PATH}/tables",
            params={"catalog_name": catalog, "schema_name": schema},
        )
        self._raise_for_status(resp)
        body = resp.json().get("tables") or []
        return [UCTable.model_validate(t) for t in body]

    async def get_table(self, catalog: str, schema: str, name: str) -> UCTable:
        full = f"{catalog}.{schema}.{name}"
        resp = await self._http.get(f"{self.BASE_PATH}/tables/{full}")
        self._raise_for_status(resp)
        return UCTable.model_validate(resp.json())

    async def create_table(
        self,
        *,
        catalog: str,
        schema: str,
        name: str,
        columns: list[dict[str, Any]],
        storage_location: str,
        data_source_format: str = "DELTA",
        table_type: Literal["MANAGED", "EXTERNAL"] = "MANAGED",
        properties: dict[str, str] | None = None,
        comment: str | None = None,
    ) -> UCTable:
        body: dict[str, Any] = {
            "name": name,
            "catalog_name": catalog,
            "schema_name": schema,
            "table_type": table_type,
            "data_source_format": data_source_format,
            "storage_location": storage_location,
            "columns": columns,
            "properties": properties or {},
            "comment": comment or "",
        }
        resp = await self._http.post(f"{self.BASE_PATH}/tables", json=body)
        self._raise_for_status(resp)
        return UCTable.model_validate(resp.json())

    async def delete_table(self, catalog: str, schema: str, name: str) -> None:
        full = f"{catalog}.{schema}.{name}"
        resp = await self._http.delete(f"{self.BASE_PATH}/tables/{full}")
        self._raise_for_status(resp)

    # --- Permissions ---

    async def update_permissions(
        self,
        securable_type: str,
        full_name: str,
        *,
        principal: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> None:
        """PATCH a securable's permissions for one principal (D10 mirror)."""
        change: dict[str, Any] = {"principal": principal}
        if add:
            change["add"] = add
        if remove:
            change["remove"] = remove
        resp = await self._http.patch(
            f"{self.BASE_PATH}/permissions/{securable_type}/{full_name}",
            json={"changes": [change]},
        )
        self._raise_for_status(resp)

    # --- Temporary table credentials ---

    async def gen_temp_creds(
        self,
        *,
        table_id: str,
        operation: Literal["READ", "READ_WRITE"] = "READ_WRITE",
    ) -> UCTemporaryCredentials:
        resp = await self._http.post(
            f"{self.BASE_PATH}/temporary-table-credentials",
            json={"table_id": table_id, "operation": operation},
        )
        self._raise_for_status(resp)
        return UCTemporaryCredentials.model_validate(resp.json())
