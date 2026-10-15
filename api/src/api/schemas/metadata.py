from typing import Literal

from pydantic import BaseModel


class MetadataColumnOut(BaseModel):
    name: str
    # DuckDB's own spelling, as DESCRIBE prints it.
    type: str
    nullable: bool


class MetadataTableOut(BaseModel):
    name: str
    table_type: str
    # In table order. Null unless columns were asked for, or when the table could
    # not be described (dropped since it was listed, or a type DuckDB cannot read).
    columns: list[MetadataColumnOut] | None = None


class MetadataSchemaOut(BaseModel):
    name: str
    tables: list[MetadataTableOut]


class MetadataCatalogOut(BaseModel):
    name: str
    kind: str
    access_mode: Literal["open", "scoped"]
    schemas: list[MetadataSchemaOut]


class WorkspaceMetadataOut(BaseModel):
    """What a caller may see in a workspace, as one report: the shape a driver's
    catalog, schema, table and column listings are answered from."""

    catalogs: list[MetadataCatalogOut]
