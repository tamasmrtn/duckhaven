"""Pydantic request/response shapes for the schemas + tables endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Restricted set of DuckDB scalars exposed in the Create-Table dialog.
# Widening this is a docs change + a small mapping change below — DuckDB
# supports more types, but the M3 UI only offers these and we want the
# control plane to reject anything outside the offered set as a guard
# against accidental misuse.
AllowedColumnType = Literal[
    "INTEGER",
    "BIGINT",
    "DOUBLE",
    "VARCHAR",
    "BOOLEAN",
    "DATE",
    "TIMESTAMP",
    "DECIMAL",
]


class CatalogSchemaCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class CatalogSchemaOut(BaseModel):
    name: str
    catalog_name: str


class ColumnSpec(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: AllowedColumnType
    nullable: bool = True


class TableCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    columns: list[ColumnSpec] = Field(min_length=1)


class TableColumnOut(BaseModel):
    name: str
    type_text: str
    type_name: str
    position: int
    nullable: bool


class TableOut(BaseModel):
    name: str
    schema_name: str
    catalog_name: str
    table_type: str
    data_source_format: str
    storage_location: str | None = None
    columns: list[TableColumnOut] = Field(default_factory=list)
    properties: dict[str, str] = Field(default_factory=dict)
    table_id: str | None = None
