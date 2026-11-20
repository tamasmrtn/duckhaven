"""The ``duckhaven`` system catalog's tables, as the catalog tree lists them."""

from __future__ import annotations

from pydantic import BaseModel


class SystemColumnOut(BaseModel):
    name: str
    type: str


class SystemTableOut(BaseModel):
    schema_name: str
    name: str
    description: str
    # The permission reading it takes; null for a table every member reads, its
    # rows narrowed to what they can see.
    permission: str | None
    columns: list[SystemColumnOut]


class SystemCatalogOut(BaseModel):
    catalog: str
    # False while its database cannot be reached: a query on it would fail.
    available: bool
    # Only the tables the caller may read.
    tables: list[SystemTableOut]
