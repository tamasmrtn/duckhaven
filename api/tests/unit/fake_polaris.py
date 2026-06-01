"""In-memory `PolarisClient` stand-in for unit tests.

Mirrors the public surface of `api.services.polaris.PolarisClient` just
enough for routers to exercise their happy + error paths without a real
Polaris server. Each instance is isolated, so tests can pre-seed
catalogs / namespaces / tables or toggle failure flags.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from api.services.polaris import (
    PolarisCatalog,
    PolarisConflictError,
    PolarisError,
    PolarisNotFoundError,
    PolarisSchema,
    PolarisTable,
    _columns_from_iceberg_schema,
)


class FakePolaris:
    def __init__(self) -> None:
        self.catalogs: dict[str, PolarisCatalog] = {}
        self.schemas: dict[tuple[str, str], PolarisSchema] = {}
        self.tables: dict[tuple[str, str, str], PolarisTable] = {}
        # Test knobs:
        self.fail_create_catalog: bool = False
        self.fail_create_schema: bool = False
        self.fail_create_table: bool = False
        self.created_table_bodies: list[dict[str, Any]] = []

    # --- Catalogs ---

    async def create_catalog(
        self,
        name: str,
        *,
        storage_type: str,
        base_location: str,
        allowed_locations: list[str] | None = None,
        extra_storage: dict[str, Any] | None = None,
    ) -> PolarisCatalog:
        if self.fail_create_catalog:
            raise PolarisError("simulated create_catalog failure")
        if name in self.catalogs:
            raise PolarisConflictError(f"catalog {name} already exists")
        cat = PolarisCatalog(name=name)
        self.catalogs[name] = cat
        return cat

    async def get_catalog(self, name: str) -> PolarisCatalog:
        if name not in self.catalogs:
            raise PolarisNotFoundError(name)
        return self.catalogs[name]

    async def catalog_exists(self, name: str) -> bool:
        return name in self.catalogs

    async def delete_catalog(self, name: str) -> None:
        self.catalogs.pop(name, None)
        for key in [k for k in self.schemas if k[0] == name]:
            self.schemas.pop(key, None)
        for key in [k for k in self.tables if k[0] == name]:
            self.tables.pop(key, None)

    # --- Namespaces ---

    async def create_schema(self, catalog: str, name: str) -> PolarisSchema:
        if self.fail_create_schema:
            raise PolarisError("simulated create_schema failure")
        if (catalog, name) in self.schemas:
            raise PolarisConflictError(f"schema {catalog}.{name} already exists")
        sc = PolarisSchema(name=name, catalog_name=catalog)
        self.schemas[(catalog, name)] = sc
        return sc

    async def list_schemas(self, catalog: str) -> list[PolarisSchema]:
        return [s for (c, _), s in self.schemas.items() if c == catalog]

    # --- Tables ---

    async def list_tables(self, catalog: str, schema: str) -> list[PolarisTable]:
        # Iceberg REST list returns identifiers only (no columns).
        return [
            PolarisTable(name=t.name, catalog_name=catalog, schema_name=schema)
            for (c, s, _), t in self.tables.items()
            if c == catalog and s == schema
        ]

    async def get_table(self, catalog: str, schema: str, name: str) -> PolarisTable:
        key = (catalog, schema, name)
        if key not in self.tables:
            raise PolarisNotFoundError(f"{catalog}.{schema}.{name}")
        return self.tables[key]

    async def create_table(
        self,
        *,
        catalog: str,
        schema: str,
        name: str,
        columns: list[dict[str, Any]],
        comment: str | None = None,
    ) -> PolarisTable:
        if self.fail_create_table:
            raise PolarisError("simulated create_table failure")
        key = (catalog, schema, name)
        if key in self.tables:
            raise PolarisConflictError(f"table {catalog}.{schema}.{name} already exists")
        self.created_table_bodies.append(
            {"catalog": catalog, "schema": schema, "name": name, "columns": columns}
        )
        table = PolarisTable(
            name=name,
            catalog_name=catalog,
            schema_name=schema,
            table_id=str(uuid4()),
            storage_location=f"file:///w/{catalog}/{schema}/{name}/metadata/v1.json",
            columns=_columns_from_iceberg_schema({"fields": columns}),
            properties={},
        )
        self.tables[key] = table
        return table

    async def delete_table(self, catalog: str, schema: str, name: str) -> None:
        key = (catalog, schema, name)
        if key not in self.tables:
            raise PolarisNotFoundError(f"{catalog}.{schema}.{name}")
        self.tables.pop(key, None)

    async def aclose(self) -> None:
        return None
