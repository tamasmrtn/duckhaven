"""Listing a workspace's catalogs, schemas and tables across every backend at once.

The fan-out search and the metadata report share: each catalog's backend is asked
concurrently, and a catalog that is stale or unreachable is skipped (and logged)
rather than failing the whole listing. Grant redaction is the caller's: what a
caller may see depends on what it is going to show.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from api.models.catalog import Catalog
from api.services.catalog_backends import (
    CatalogBackendError,
    CatalogSchemaInfo,
    CatalogTableInfo,
    backend_for,
)

logger = logging.getLogger(__name__)


@dataclass
class ListedCatalog:
    catalog: Catalog
    schemas: list[CatalogSchemaInfo]
    # Tables per schema name, filled only when tables were asked for.
    tables: dict[str, list[CatalogTableInfo]] = field(default_factory=dict)


async def list_catalogs(
    catalogs: list[Catalog],
    polaris: Any,
    *,
    with_tables: bool,
    schema: str | None = None,
) -> list[ListedCatalog]:
    """Each reachable catalog with its schemas, and their tables if ``with_tables``.

    ``schema`` narrows the listing to that one schema (and its tables). The
    listing calls hold no shared state, so they run concurrently; a backend error
    for one catalog or schema leaves it out instead of failing the rest.
    """
    backends = [backend_for(cat, polaris=polaris) for cat in catalogs]
    schemas_per_catalog = await asyncio.gather(
        *(backend.list_schemas(cat) for cat, backend in zip(catalogs, backends, strict=True)),
        return_exceptions=True,
    )
    listed: list[ListedCatalog] = []
    backend_by_slug = {}
    for cat, backend, schemas in zip(catalogs, backends, schemas_per_catalog, strict=True):
        if isinstance(schemas, CatalogBackendError):
            logger.warning("Listing skipped catalog=%s: %s", cat.slug, schemas)
            continue
        if isinstance(schemas, BaseException):
            raise schemas
        if schema is not None:
            schemas = [s for s in schemas if s.name == schema]
        listed.append(ListedCatalog(cat, schemas))
        backend_by_slug[cat.slug] = backend
    if not with_tables:
        return listed

    lookups = [(entry, s) for entry in listed for s in entry.schemas]
    tables_per_schema = await asyncio.gather(
        *(backend_by_slug[e.catalog.slug].list_tables(e.catalog, s.name) for e, s in lookups),
        return_exceptions=True,
    )
    for (entry, s), tables in zip(lookups, tables_per_schema, strict=True):
        if isinstance(tables, CatalogBackendError):
            logger.warning("Listing skipped schema=%s.%s: %s", entry.catalog.slug, s.name, tables)
            tables = []
        elif isinstance(tables, BaseException):
            raise tables
        entry.tables[s.name] = tables
    return listed
