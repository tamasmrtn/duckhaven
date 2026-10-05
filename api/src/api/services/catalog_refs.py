"""Which of a workspace's catalogs a statement's connection has to attach.

Attaching a DuckLake catalog costs a Postgres connection and ~60 ms, so a
workspace with many of them must not attach them all for every statement. The
statement is scanned for the catalogs it could name, and only those (plus the
active catalog, for unqualified names) are attached.

The scan is a deliberate over-approximation: every identifier-shaped word in
the SQL, including inside string literals and quoted identifiers, is compared
with the workspace's catalog slugs, case-insensitively. Parsing would miss
names the grammar does not expose as tables -- `USE c`, `CREATE SCHEMA c.s`,
`CALL c.merge_adjacent_files()`, `ducklake_snapshots('c')`,
`query('SELECT … FROM c.s.t')`, anything sqlglot only parses as a command -- and
a word that merely looks like a catalog costs one unneeded attach. What no scan
can see, a view or macro reading another catalog, the agent fetches on demand
(see `catalog_requests`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from api.models.agent import Agent
from api.models.catalog import Catalog
from api.services.agent_capabilities import agent_supports_feature
from api.services.session_credentials import build_catalog_attach

# The agent protocol feature that says it attaches catalogs on demand.
ON_DEMAND_ATTACH = "on_demand_attach"

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Words that mean a statement lists objects across every attached catalog. Those
# listings are only complete with everything attached, so such a statement
# attaches the whole workspace, as every statement used to.
_ENUMERATION_WORDS = frozenset(
    {
        "information_schema",
        "pg_catalog",
        "sqlite_master",
        "sqlite_schema",
        "show_tables",
        "show_tables_expanded",
        "show_databases",
        "database_list",
    }
)
# `SHOW TABLES`, `SHOW ALL TABLES`, `SHOW DATABASES`, and a bare `DESCRIBE`/`SHOW`.
_LISTING_STATEMENT = re.compile(r"(?:\A|;)\s*(?:show\b|describe\s*(?:;|\Z))", re.IGNORECASE)


def lists_catalog_objects(sql: str) -> bool:
    """Whether ``sql`` lists objects across catalogs (``information_schema``,
    ``duckdb_tables()`` and friends, ``SHOW``, the listing ``PRAGMA``s)."""
    if _LISTING_STATEMENT.search(sql):
        return True
    return any(
        word in _ENUMERATION_WORDS or word.startswith("duckdb_")
        for word in (w.lower() for w in _WORD.findall(sql))
    )


def statement_catalogs(
    sql: str, catalogs: list[Catalog], active_catalog: str | None, *, also: Iterable[str] = ()
) -> list[Catalog]:
    """The catalogs to attach for ``sql``, in workspace order.

    ``also`` names catalogs the caller knows it needs whatever the text says, such
    as the table an internal stats or health probe reads.
    """
    if lists_catalog_objects(sql):
        return list(catalogs)
    wanted = {w.lower() for w in _WORD.findall(sql)} | {s.lower() for s in also}
    if active_catalog:
        wanted.add(active_catalog.lower())
    return [c for c in catalogs if c.slug.lower() in wanted]


async def dispatch_catalogs(
    agent: Agent | None, catalogs: list[Catalog], attach: list[Catalog]
) -> dict[str, object]:
    """The catalog fields of a dispatch frame: everything for an agent that
    attaches up front, ``attach`` plus what to prepare for one that attaches on
    demand.

    ``workspace_catalogs`` is what the agent may ask for later, and its presence
    is how the agent knows to ask. ``preload`` is every kind it may have to
    attach, whose extensions must be loaded before its sandbox is locked.
    """
    capabilities = agent.capabilities if agent is not None else None
    if not agent_supports_feature(capabilities, ON_DEMAND_ATTACH):
        return {"catalogs": [await build_catalog_attach(c) for c in catalogs]}
    return {
        "catalogs": [await build_catalog_attach(c) for c in attach],
        "workspace_catalogs": [c.slug for c in catalogs],
        "preload": {
            "catalog_kinds": sorted({c.kind for c in catalogs}),
            "backend_kinds": sorted({c.storage_backend.kind for c in catalogs}),
        },
    }
