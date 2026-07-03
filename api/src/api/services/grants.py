"""Scoped catalog/schema/table access grants (issue #129).

A :class:`~api.models.catalog_grant.CatalogGrant` narrows a principal's access
below the catalog when the catalog's attachment is in ``access_mode="scoped"``
(:class:`~api.models.catalog.WorkspaceCatalog`). ``"open"`` attachments — the
default — fall back to the plain workspace role, so this module is a no-op for
teams that never opt in.

The tier vocabulary extends the workspace roles with a discovery-only level:
``metadata < reader < writer`` (``owner`` is a workspace role only, never
grantable here). Resolution is a hierarchy walk — a grant at a coarser node
(catalog, or a schema) covers every finer node beneath it, including tables
created *after* the grant ("future grants for free", matching Unity Catalog's
inheriting model). Grants are additive: the effective tier at a node is the
highest covering grant, then **capped** at the principal's workspace role so a
grant can only narrow access, never promote a ``reader`` past ``writer``.

Object-reference extraction (:func:`extract_table_refs`) uses ``sqlglot`` as a
pure-Python parser — it never opens a DuckDB connection or executes SQL, so the
control plane's "never run user SQL" invariant (I1) is preserved.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlglot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp

from api.models.catalog import Catalog, WorkspaceCatalog
from api.models.catalog_grant import CatalogGrant
from api.models.workspace import WorkspaceMember

# Grantable tiers, low → high. `owner` is intentionally absent (workspace role).
TIER_SCALE: dict[str, int] = {"metadata": 0, "reader": 1, "writer": 2}
_TIER_BY_RANK: dict[int, str] = {v: k for k, v in TIER_SCALE.items()}

# A workspace role's ceiling on the tier scale. `owner` adds only membership
# management over `writer`, which is meaningless per-object, so it caps at
# `writer`. Unknown roles fall back to the least privilege.
ROLE_CAP: dict[str, str] = {"reader": "reader", "writer": "writer", "owner": "writer"}

# Built-in discovery surfaces that are never grant-checked: the read-only system
# catalog and the metadata namespaces. This is what lets a `metadata`-tier
# principal query `information_schema` while being denied table rows.
SYSTEM_CATALOGS: frozenset[str] = frozenset({"duckhaven", "system", "temp"})
METADATA_SCHEMAS: frozenset[str] = frozenset({"info_schema", "information_schema"})


class GrantDenied(ValueError):
    """Raised when a scoped grant check rejects a query before dispatch.

    A ``ValueError`` subclass so the scheduler's existing ``except ValueError``
    records the run as failed; ``routers/queries.py`` catches it explicitly to
    return HTTP 403.
    """


@dataclass(frozen=True)
class TableRef:
    """A table referenced by a statement, as parsed (names may be unqualified)."""

    catalog: str | None
    schema: str | None
    table: str
    is_target: bool  # True = the write target (needs `writer`), else a source


def tier_rank(tier: str | None) -> int:
    """Rank of a tier on the scale; -1 for None (no access)."""
    return TIER_SCALE[tier] if tier is not None else -1


def _covers(grant: CatalogGrant, schema: str | None, table: str | None) -> bool:
    """True if ``grant`` is an ancestor-or-equal of the ``(schema, table)`` node."""
    if grant.schema_name is None:  # catalog-level (table_name NULL by CHECK)
        return True
    if grant.table_name is None:  # schema-level: covers the schema and its tables
        return grant.schema_name == schema
    return grant.schema_name == schema and grant.table_name == table  # exact table


def access_tier(
    grants: list[CatalogGrant], member_role: str | None, schema: str | None, table: str | None
) -> str | None:
    """Effective tier a principal has *at* a node, or None for no access.

    ``grants`` are the principal's grants on one catalog; ``member_role`` their
    workspace role. Highest covering grant, capped at the role. Used to
    authorize acting on a specific object (leaf read/write, dispatch).
    """
    if member_role is None:
        return None
    best: int | None = None
    for g in grants:
        if _covers(g, schema, table):
            r = TIER_SCALE[g.tier]
            best = r if best is None else max(best, r)
    if best is None:
        return None
    cap = TIER_SCALE[ROLE_CAP.get(member_role, "reader")]
    return _TIER_BY_RANK[min(best, cap)]


def schema_visible(grants: list[CatalogGrant], schema: str) -> bool:
    """True if a principal may discover ``schema`` (for list filtering).

    Visible when a grant covers the schema (catalog- or schema-level) *or* is
    nested within it (a table-level grant in that schema) — so a principal can
    navigate to the objects they were granted without the whole schema list
    leaking.
    """
    return any(g.schema_name is None or g.schema_name == schema for g in grants)


# --- DB-aware wrappers used by the enforcement points -----------------------


async def attachment_access_mode(
    db: AsyncSession, workspace_id: uuid.UUID, catalog_id: uuid.UUID
) -> str:
    """The ``access_mode`` of a workspace's catalog attachment (``open`` default)."""
    mode = (
        await db.execute(
            select(WorkspaceCatalog.access_mode).where(
                WorkspaceCatalog.workspace_id == workspace_id,
                WorkspaceCatalog.catalog_id == catalog_id,
            )
        )
    ).scalar_one_or_none()
    return mode or "open"


async def _member_role(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
    return (
        await db.execute(
            select(WorkspaceMember.role).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def load_grants(
    db: AsyncSession, user_id: uuid.UUID, catalog_id: uuid.UUID
) -> list[CatalogGrant]:
    return list(
        (
            await db.execute(
                select(CatalogGrant).where(
                    CatalogGrant.user_id == user_id,
                    CatalogGrant.catalog_id == catalog_id,
                )
            )
        )
        .scalars()
        .all()
    )


async def node_tier(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    catalog: Catalog,
    user_id: uuid.UUID,
    schema: str | None,
    table: str | None,
) -> str | None:
    """Effective tier for a principal at a node in a *scoped* catalog.

    Only meaningful when the attachment is scoped (callers gate on
    :func:`attachment_access_mode`); loads the principal's grants + role.
    """
    grants = await load_grants(db, user_id, catalog.id)
    role = await _member_role(db, workspace_id, user_id)
    return access_tier(grants, role, schema, table)


async def visible_schemas(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    catalog: Catalog,
    user_id: uuid.UUID,
    names: list[str],
) -> set[str]:
    """Subset of ``names`` the principal may discover in a scoped catalog."""
    grants = await load_grants(db, user_id, catalog.id)
    return {n for n in names if schema_visible(grants, n)}


# --- SQL object-reference extraction ----------------------------------------

_WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop)


def _target_table(stmt: exp.Expression) -> exp.Table | None:
    """The write target table of a statement, or None for a pure read."""
    if not isinstance(stmt, _WRITE_NODES):
        return None
    this = stmt.this
    if isinstance(this, exp.Table):
        return this
    return this.find(exp.Table) if this is not None else None


def extract_table_refs(sql: str) -> list[TableRef]:
    """Statically parse the ``catalog.schema.table`` refs a query touches.

    Parses every statement in DuckDB dialect, drops names that are in-query CTE
    aliases, and tags each ref as the write target vs. a read source. Raises
    :class:`GrantDenied` on a parse failure (fail-closed): an unparseable query
    is never allowed through a scoped catalog.
    """
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as exc:  # sqlglot.errors.ParseError et al
        raise GrantDenied(f"Could not parse SQL for grant check: {exc}") from exc

    refs: list[TableRef] = []
    for stmt in statements:
        if stmt is None:
            continue
        cte_names = {c.alias_or_name for c in stmt.find_all(exp.CTE)}
        target = _target_table(stmt)
        for t in stmt.find_all(exp.Table):
            cat = t.catalog or None
            schema = t.db or None
            # An unqualified name matching a CTE alias is not a real table.
            if cat is None and schema is None and t.name in cte_names:
                continue
            refs.append(TableRef(catalog=cat, schema=schema, table=t.name, is_target=t is target))
    return refs


def is_exempt_ref(catalog: str | None, schema: str | None) -> bool:
    """True for the built-in discovery surfaces (system catalog / info schema)."""
    return (catalog in SYSTEM_CATALOGS) or (schema in METADATA_SCHEMAS)
