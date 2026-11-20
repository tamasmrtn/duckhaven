"""Which tables a finished query read and wrote, worked out from its SQL, for
``access.table_access``.

Names resolve as DuckDB binds them: an unqualified name in the query's current
catalog and schema, and a two-part ``x.t`` in catalog ``x``'s ``main`` schema when
``x`` is a catalog, else in schema ``x`` of the current catalog. Names are kept in
lower case, as DuckDB matches them. A name that resolves to no catalog DuckHaven
knows (a temporary table, or the system catalog) is left out, and so is a query
whose SQL does not parse.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlglot

from api.services.grants import context_after, is_exempt_ref, statement_refs
from api.services.workspace import DEFAULT_SCHEMA

# The schema DuckDB binds a two-part `catalog.table` to.
_CATALOG_DEFAULT_SCHEMA = "main"


@dataclass(frozen=True, order=True)
class Access:
    catalog_name: str
    schema_name: str
    table_name: str
    access: str  # "read" or "write"


def accesses(
    sql: str,
    active_catalog: str | None,
    active_schema: str | None,
    catalogs: dict[str, tuple[uuid.UUID, str]],
) -> list[Access]:
    """The tables ``sql`` read and wrote, each once per kind of access.

    ``catalogs`` maps each catalog's lower-cased name to its id and name;
    ``active_catalog`` is None when where unqualified names resolve is unknown, and
    they are then left out. A ``DESCRIBE`` reads no rows, so it is not an access.
    """
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except Exception:  # noqa: BLE001 - sqlglot raises several kinds
        return []
    context = (active_catalog, active_schema or DEFAULT_SCHEMA) if active_catalog else None
    found: set[Access] = set()
    for stmt in statements:
        for ref in statement_refs(stmt):
            named_catalog = ref.catalog is not None and ref.catalog.lower() in catalogs
            if ref.is_metadata_only or (
                is_exempt_ref(ref.catalog, ref.schema) and not named_catalog
            ):
                continue
            if ref.catalog:
                where = (ref.catalog, ref.schema or _CATALOG_DEFAULT_SCHEMA)
            elif ref.schema and ref.schema.lower() in catalogs:
                where = (ref.schema, _CATALOG_DEFAULT_SCHEMA)
            elif context is None:
                continue
            else:
                where = (context[0], ref.schema or context[1])
            named = catalogs.get(where[0].lower())
            if named is None:
                continue
            found.add(
                Access(
                    named[1],
                    where[1].lower(),
                    ref.table.lower(),
                    "write" if ref.is_target else "read",
                )
            )
        context = context_after(stmt, context)
    return sorted(found)
