"""Deciding what of a lineage graph the caller is allowed to see.

Lineage is not harmless metadata: a graph names tables, and a table name can
carry more than the rows do. Two different rules apply, and conflating them would
be wrong in both directions:

**Outside the workspace — pruned.** A node in a catalog the requesting workspace
does not attach is not "hidden", it is out of scope entirely, in the same way
cross-workspace joins are. It and its edges disappear.

**Inside the workspace, without a grant — redacted.** A node in an attached but
``scoped`` catalog that the principal holds no tier on keeps its place in the
graph under an opaque, stable key, with every name field withheld. The shape,
the distances and the fact that *something* sits there survive; the identity does
not. Pruning here would be worse than useless: it silently shortens paths, so a
partial graph becomes indistinguishable from a complete one.

Catalogs attached ``open`` are not redacted at all, matching the rest of the
grant system's no-op behaviour for workspaces that never opted in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from api.models.catalog import Catalog
from api.services.grants import is_scoped, node_tier
from api.services.lineage.keys import redacted_key


@dataclass(frozen=True)
class VisibleNode:
    """One node as the caller may see it."""

    key: str
    kind: str  # "table" | "external" | "redacted"
    catalog: str | None
    schema: str | None
    table: str | None
    system: str | None
    distance: int


class Visibility:
    """Per-request cache of "may this principal see this catalog's objects".

    Resolving a tier hits the grants tables, and a graph commonly names the same
    catalog dozens of times, so both the scoped-ness of an attachment and the
    tier at each node are memoized for the life of one request.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        principal_id: uuid.UUID | None,
        catalogs: list[Catalog],
    ) -> None:
        self._db = db
        self._workspace_id = workspace_id
        self._principal_id = principal_id
        self._by_id = {c.id: c for c in catalogs}
        self._scoped: dict[uuid.UUID, bool] = {}
        self._tiers: dict[tuple[uuid.UUID, str, str], str | None] = {}

    def attaches(self, catalog_id: uuid.UUID | None) -> bool:
        """Whether the requesting workspace can see this catalog at all."""
        return catalog_id in self._by_id

    def slug(self, catalog_id: uuid.UUID) -> str:
        return self._by_id[catalog_id].slug

    async def _is_scoped(self, catalog: Catalog) -> bool:
        if catalog.id not in self._scoped:
            self._scoped[catalog.id] = await is_scoped(self._db, self._workspace_id, catalog)
        return self._scoped[catalog.id]

    async def may_see(self, catalog_id: uuid.UUID, schema: str, table: str) -> bool:
        """Whether the principal holds at least ``metadata`` on this node."""
        catalog = self._by_id[catalog_id]
        if not await self._is_scoped(catalog):
            return True  # open attachment: unrestricted, as everywhere else
        if self._principal_id is None:
            return False
        cache_key = (catalog_id, schema, table)
        if cache_key not in self._tiers:
            self._tiers[cache_key] = await node_tier(
                self._db,
                self._workspace_id,
                catalog,
                self._principal_id,
                schema,
                table,
            )
        return self._tiers[cache_key] is not None


async def visible_node(
    visibility: Visibility,
    *,
    key: str,
    catalog_id: uuid.UUID | None,
    system: str | None,
    schema: str,
    table: str,
    distance: int,
) -> VisibleNode | None:
    """One node as the caller may see it, or ``None`` when it is out of scope.

    External assets are never redacted: they are names an importer chose for
    systems outside DuckHaven's authority, carrying no DuckHaven data and covered
    by no DuckHaven grant.
    """
    if catalog_id is None:
        return VisibleNode(
            key=key,
            kind="external",
            catalog=None,
            schema=schema,
            table=table,
            system=system,
            distance=distance,
        )
    if not visibility.attaches(catalog_id):
        return None
    if not await visibility.may_see(catalog_id, schema, table):
        return VisibleNode(
            key=redacted_key(key),
            kind="redacted",
            catalog=None,
            schema=None,
            table=None,
            system=None,
            distance=distance,
        )
    return VisibleNode(
        key=key,
        kind="table",
        catalog=visibility.slug(catalog_id),
        schema=schema,
        table=table,
        system=system,
        distance=distance,
    )
