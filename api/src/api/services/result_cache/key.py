"""The identity of a cached query result.

Two runs may share a result when they are the same query (the same DuckDB parse
tree) asked in the same context: the same workspace, the same catalog and schema
for unqualified names, the same catalogs attached for it (the ones it names, see
`catalog_refs`), and the same engine settings that can change a value without
changing the data — the DuckDB runtime and the session time zone.

Table versions are deliberately *not* part of the key. They are stored on the
entry and compared on every lookup, so a query keeps one entry whose versions
are refreshed when a compaction-style commit is proven harmless, instead of a
new entry per version.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass

# Bumped whenever what goes into the key changes meaning, so an upgrade never
# reads entries written under different rules.
# 2: `attached` holds the catalogs the statement names, not every workspace catalog.
KEY_VERSION = 2


@dataclass(frozen=True)
class CacheContext:
    """What, besides the query text and table data, decides the result."""

    current_catalog: str
    current_schema: str
    runtime_id: str | None
    timezone: str


@dataclass(frozen=True)
class AttachedCatalog:
    """One catalog the query's connection attaches. The storage backend is part
    of it so a storage migration's cutover starts a fresh entry."""

    catalog_id: str
    slug: str
    kind: str
    storage_backend_id: str | None


def cache_key(
    *,
    workspace_id: uuid.UUID,
    canonical: str,
    context: CacheContext,
    attached: list[AttachedCatalog],
) -> str:
    payload = {
        "v": KEY_VERSION,
        "workspace": str(workspace_id),
        "query": canonical,
        "context": asdict(context),
        "attached": sorted((asdict(a) for a in attached), key=lambda a: a["catalog_id"]),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
