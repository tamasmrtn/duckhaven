"""Pydantic request/response shapes for the schemas + tables endpoints."""

from __future__ import annotations

from datetime import datetime
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
    # ``catalog`` is the catalog slug (DuckDB alias / addressing); ``catalog_name``
    # is the Polaris warehouse name, retained for compatibility.
    catalog: str
    catalog_name: str
    workspace_id: str


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
    # Simple display type the UI renders (web/src/types/catalog.ts TableColumn.type).
    type: str
    position: int
    nullable: bool


class TableOut(BaseModel):
    name: str
    schema_name: str
    # ``catalog`` is the catalog slug; ``catalog_name`` is the Polaris name.
    catalog: str
    catalog_name: str
    workspace_id: str
    table_type: str
    data_source_format: str
    # Alias the UI consumes (web/src/types/catalog.ts CatalogTable.format).
    format: str
    storage_location: str | None = None
    columns: list[TableColumnOut] = Field(default_factory=list)
    properties: dict[str, str] = Field(default_factory=dict)
    table_id: str | None = None
    # Control-plane metadata (TableMetadata); null until first tracked event.
    catalog_commits: bool = False
    row_count: int | None = None
    size_bytes: int | None = None
    owner: str | None = None
    last_write_at: datetime | None = None
    last_write_by: str | None = None
    last_write_agent: str | None = None
    # Iceberg-native metadata (web/src/types/catalog.ts CatalogTable).
    # snapshot_id is a string: Iceberg 64-bit ids exceed JS's safe-integer range.
    format_version: int | None = None
    snapshot_id: str | None = None
    snapshot_at: datetime | None = None
    data_file_count: int | None = None
    has_deletes: bool | None = None


class SnapshotOut(BaseModel):
    """One row of a table's Iceberg snapshot history (read live from Polaris).

    Ids are strings: Iceberg 64-bit snapshot ids exceed JS's safe-integer
    range. Metric fields come from the Iceberg snapshot `summary` and are
    null when the commit did not record them."""

    snapshot_id: str
    parent_snapshot_id: str | None = None
    committed_at: datetime
    operation: str | None = None
    is_current: bool = False
    schema_id: int | None = None
    added_records: int | None = None
    deleted_records: int | None = None
    total_records: int | None = None
    added_data_files: int | None = None
    total_data_files: int | None = None
