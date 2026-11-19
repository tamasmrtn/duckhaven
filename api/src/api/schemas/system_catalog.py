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
    columns: list[SystemColumnOut]


class SystemCatalogOut(BaseModel):
    catalog: str
    # False while its database cannot be reached: a query on it would fail.
    available: bool
    tables: list[SystemTableOut]
