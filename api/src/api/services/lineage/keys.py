"""The canonical asset key: how a dataset is named inside the lineage graph.

One indexed string per edge endpoint, so traversal is an equality/IN lookup on a
single column and the dedup index is three columns rather than eight with
``coalesce``. The structured name columns on :class:`~api.models.lineage.LineageEdge`
are kept for display and cleanup; this is the join key.

Internal assets key on ``catalogs.id`` rather than the catalog slug, so renaming a
catalog leaves its lineage intact — the slug is display-only. A *table* rename is
a different matter and does break continuity: the renamed table has a new key, so
old edges are orphaned until the old name is dropped (which deletes them). That
is a real limitation, documented in ``docs/concepts/lineage.md`` rather than
papered over.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

# Bumping this changes every key, orphaning the whole graph — don't, without a
# data migration that rewrites `source_key`/`target_key`.
_INTERNAL_PREFIX = "cat"
_EXTERNAL_PREFIX = "ext"


@dataclass(frozen=True)
class AssetRef:
    """One edge endpoint, resolved to the identity the graph stores.

    Exactly one of ``catalog_id`` / ``system`` is set: an asset either lives in a
    DuckHaven catalog or it is external to DuckHaven entirely.
    """

    schema: str
    table: str
    catalog_id: uuid.UUID | None = None
    system: str | None = None

    @property
    def key(self) -> str:
        return asset_key(
            schema=self.schema, table=self.table, catalog_id=self.catalog_id, system=self.system
        )

    @property
    def is_external(self) -> bool:
        return self.catalog_id is None


def asset_key(
    *,
    schema: str,
    table: str,
    catalog_id: uuid.UUID | None = None,
    system: str | None = None,
) -> str:
    """The canonical key for one asset.

    ``cat:<catalog_uuid>/<schema>/<table>`` for a dataset in a DuckHaven catalog,
    ``ext:<system>/<schema>/<table>`` for one outside it.
    """
    if catalog_id is not None:
        return f"{_INTERNAL_PREFIX}:{catalog_id}/{schema}/{table}"
    if not system:
        raise ValueError("An external asset needs a system name")
    return f"{_EXTERNAL_PREFIX}:{system}/{schema}/{table}"


def internal_ref(catalog_id: uuid.UUID, schema: str, table: str) -> AssetRef:
    """An asset inside a DuckHaven catalog."""
    return AssetRef(schema=schema, table=table, catalog_id=catalog_id)


def external_ref(system: str, schema: str, table: str) -> AssetRef:
    """An asset outside DuckHaven, named by whichever producer reported it."""
    return AssetRef(schema=schema, table=table, system=system)


def redacted_key(key: str) -> str:
    """A stable opaque stand-in for a key the caller may not see.

    Hashed rather than randomised so the same hidden node collapses to one node
    across every edge that touches it and across repeated requests — the graph
    keeps its shape and its distances while the name stays withheld. Truncated to
    16 hex chars: enough that a collision is not a practical concern within one
    bounded graph, short enough to stay readable in a payload.
    """
    return "redacted:" + hashlib.sha256(key.encode()).hexdigest()[:16]
