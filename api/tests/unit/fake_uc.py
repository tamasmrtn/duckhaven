"""In-memory `UCClient` stand-in for unit tests.

Mirrors the public surface of `api.services.unity_catalog.UCClient`
just enough for routers to exercise their happy + error paths without
a real UC server. Each instance is isolated, so tests can pre-seed
catalogs / schemas / tables or toggle failure flags.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from api.services.unity_catalog import (
    UCCatalog,
    UCConflictError,
    UCError,
    UCNotFoundError,
    UCSchema,
    UCTable,
    UCTemporaryCredentials,
)


class FakeUC:
    def __init__(self) -> None:
        self.catalogs: dict[str, UCCatalog] = {}
        self.schemas: dict[tuple[str, str], UCSchema] = {}
        self.tables: dict[tuple[str, str, str], UCTable] = {}
        # Test knobs:
        self.fail_create_catalog: bool = False
        self.fail_create_schema: bool = False
        self.fail_create_table: bool = False
        self.created_table_bodies: list[dict[str, Any]] = []

    # --- Catalogs ---

    async def create_catalog(self, name: str, comment: str | None = None) -> UCCatalog:
        if self.fail_create_catalog:
            raise UCError("simulated create_catalog failure")
        if name in self.catalogs:
            raise UCConflictError(f"catalog {name} already exists")
        cat = UCCatalog(name=name, comment=comment)
        self.catalogs[name] = cat
        return cat

    async def get_catalog(self, name: str) -> UCCatalog:
        if name not in self.catalogs:
            raise UCNotFoundError(name)
        return self.catalogs[name]

    async def catalog_exists(self, name: str) -> bool:
        return name in self.catalogs

    async def delete_catalog(self, name: str, *, force: bool = True) -> None:
        self.catalogs.pop(name, None)
        for key in [k for k in self.schemas if k[0] == name]:
            self.schemas.pop(key, None)
        for key in [k for k in self.tables if k[0] == name]:
            self.tables.pop(key, None)

    # --- Schemas ---

    async def create_schema(self, catalog: str, name: str, comment: str | None = None) -> UCSchema:
        if self.fail_create_schema:
            raise UCError("simulated create_schema failure")
        if (catalog, name) in self.schemas:
            raise UCConflictError(f"schema {catalog}.{name} already exists")
        sc = UCSchema(name=name, catalog_name=catalog, full_name=f"{catalog}.{name}")
        self.schemas[(catalog, name)] = sc
        return sc

    async def list_schemas(self, catalog: str) -> list[UCSchema]:
        return [s for (c, _), s in self.schemas.items() if c == catalog]

    # --- Tables ---

    async def list_tables(self, catalog: str, schema: str) -> list[UCTable]:
        return [t for (c, s, _), t in self.tables.items() if c == catalog and s == schema]

    async def get_table(self, catalog: str, schema: str, name: str) -> UCTable:
        key = (catalog, schema, name)
        if key not in self.tables:
            raise UCNotFoundError(f"{catalog}.{schema}.{name}")
        return self.tables[key]

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
        if self.fail_create_table:
            raise UCError("simulated create_table failure")
        key = (catalog, schema, name)
        if key in self.tables:
            raise UCConflictError(f"table {catalog}.{schema}.{name} already exists")
        body = {
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
        self.created_table_bodies.append(body)
        table = UCTable(
            name=name,
            catalog_name=catalog,
            schema_name=schema,
            table_id=str(uuid4()),
            table_type=table_type,
            data_source_format=data_source_format,
            storage_location=storage_location,
            columns=columns,
            properties=properties or {},
            comment=comment or "",
        )
        self.tables[key] = table
        return table

    async def delete_table(self, catalog: str, schema: str, name: str) -> None:
        key = (catalog, schema, name)
        if key not in self.tables:
            raise UCNotFoundError(f"{catalog}.{schema}.{name}")
        self.tables.pop(key, None)

    # --- Creds ---

    async def gen_temp_creds(
        self,
        *,
        table_id: str,
        operation: Literal["READ", "READ_WRITE"] = "READ_WRITE",
    ) -> UCTemporaryCredentials:
        return UCTemporaryCredentials(
            aws_temp_credentials={
                "access_key_id": "fake-key",
                "secret_access_key": "fake-secret",
                "session_token": "fake-token",
            },
            expiration_time="2099-01-01T00:00:00Z",
        )

    async def aclose(self) -> None:
        return None
