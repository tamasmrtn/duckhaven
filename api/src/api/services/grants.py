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
from fastapi import HTTPException, status
from sqlalchemy import delete, select
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
# principal query `information_schema` while being denied table rows — but only
# in a workspace with no scoped attachment. Once any catalog the workspace
# attaches is `scoped`, the metadata namespaces are rejected instead
# (`metadata_enumeration_reason`) for every session in that workspace, because the
# engine computes them across every attachment at once and cannot narrow them to
# the principal's grants.
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
    # True when the statement only reads the relation's *shape*, never its rows —
    # i.e. it sits under a `DESCRIBE`. Such a ref needs only `metadata`, matching
    # what the REST browse endpoint (`routers/schemas.py::get_table`) requires to
    # return the very same column list.
    is_metadata_only: bool = False


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


async def visible_tables(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    catalog: Catalog,
    user_id: uuid.UUID,
    schema: str,
    names: list[str],
) -> set[str]:
    """Subset of tables in ``schema`` the principal may discover (metadata+)."""
    grants = await load_grants(db, user_id, catalog.id)
    role = await _member_role(db, workspace_id, user_id)
    return {n for n in names if access_tier(grants, role, schema, n) is not None}


async def is_scoped(db: AsyncSession, workspace_id: uuid.UUID, catalog: Catalog) -> bool:
    return await attachment_access_mode(db, workspace_id, catalog.id) == "scoped"


async def enforce_leaf(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    catalog: Catalog,
    user_id: uuid.UUID,
    *,
    schema: str | None,
    table: str | None,
    need: str,
) -> None:
    """Require tier ``need`` at a node when the catalog is scoped, else 404.

    404 (not 403) at the leaf so a denied object is indistinguishable from one
    that does not exist — the workspace boundary already 403'd via membership.
    No-op for ``open`` attachments (today's behavior unchanged).
    """
    if not await is_scoped(db, workspace_id, catalog):
        return
    tier = await node_tier(db, workspace_id, catalog, user_id, schema, table)
    if tier_rank(tier) < TIER_SCALE[need]:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


async def delete_table_grants(
    db: AsyncSession, catalog_id: uuid.UUID, schema: str, table: str
) -> None:
    """Remove grants for a dropped table (mirrors ``_delete_table_meta``)."""
    await db.execute(
        delete(CatalogGrant).where(
            CatalogGrant.catalog_id == catalog_id,
            CatalogGrant.schema_name == schema,
            CatalogGrant.table_name == table,
        )
    )


async def delete_schema_grants(db: AsyncSession, catalog_id: uuid.UUID, schema: str) -> None:
    """Remove a dropped schema's grants — the schema-level grant and every
    table-level grant beneath it (catalog-level grants are untouched)."""
    await db.execute(
        delete(CatalogGrant).where(
            CatalogGrant.catalog_id == catalog_id,
            CatalogGrant.schema_name == schema,
        )
    )


# --- SQL object-reference extraction ----------------------------------------

_WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter)


def _target_tables(stmt: exp.Expression) -> list[exp.Table]:
    """The write target table(s) of a statement; empty for a pure read."""
    if isinstance(stmt, exp.TruncateTable):
        # TRUNCATE keeps its target(s) in `expressions` (`this` is None) and
        # destroys every row in them — a write, exactly like the DELETE that
        # DuckDB's grammar turns it into.
        return [t for e in stmt.expressions for t in e.find_all(exp.Table)]
    if not isinstance(stmt, _WRITE_NODES):
        return []
    this = stmt.this
    if isinstance(this, exp.Table):
        return [this]
    found = this.find(exp.Table) if this is not None else None
    return [found] if found is not None else []


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
        targets = _target_tables(stmt)
        # Tables under a DESCRIBE, by identity. Collected from anywhere in the
        # tree rather than only the top level, so `SELECT … FROM (DESCRIBE t)` —
        # the form dlt and dbt emit — is recognized as well as a bare `DESCRIBE t`.
        # Deliberately not SUMMARIZE: that one scans the rows.
        described = {id(t) for d in stmt.find_all(exp.Describe) for t in d.find_all(exp.Table)}
        for t in stmt.find_all(exp.Table):
            cat = t.catalog or None
            schema = t.db or None
            # Table *functions* (duckdb_functions(), read_parquet(...), range(...))
            # parse as a Table with an empty name — they are not catalog objects,
            # so there is nothing to grant-check.
            if not t.name:
                continue
            # An unqualified name matching a CTE alias is not a real table.
            if cat is None and schema is None and t.name in cte_names:
                continue
            is_target = any(t is x for x in targets)
            refs.append(
                TableRef(
                    catalog=cat,
                    schema=schema,
                    table=t.name,
                    is_target=is_target,
                    is_metadata_only=id(t) in described,
                )
            )
    return refs


# DuckDB meta table-functions that enumerate catalog objects. They are a second
# door onto the same listing as `information_schema` (verified live: both span
# every attached catalog), and `extract_table_refs` cannot see them — a table
# function parses as an `exp.Table` with an empty name and is skipped there — so
# they are matched here by function name instead.
_CATALOG_META_FUNCTIONS = frozenset(
    {
        "duckdb_tables",
        "duckdb_columns",
        "duckdb_views",
        "duckdb_schemas",
        "duckdb_databases",
        "duckdb_constraints",
    }
)

# Row-returning PRAGMAs with no grant-checkable object. `show_tables` and
# `database_list` enumerate; `table_info` does name one table, but only as an
# opaque string literal, so the parser sees no `exp.Table` to resolve a tier
# against — it would read any table's columns unchecked. `DESCRIBE` is the
# equivalent that *is* checkable, which is why it stays allowed. `PRAGMA version`
# touches no catalog object at all and is unaffected.
_UNCHECKABLE_PRAGMAS = frozenset({"show_tables", "database_list", "table_info"})

_ENUMERATION_HINT = (
    "Enumerating metadata is not available in a scoped catalog because the engine "
    "cannot filter it by grant. List catalogs, schemas and tables with the "
    "workspace catalog API instead, and use DESCRIBE <catalog>.<schema>.<table> "
    "for column detail."
)


def _enumeration_hint(scoped_slugs: list[str] | None) -> str:
    """The rejection hint, naming the scoped catalogs that caused it when known.

    The denial is workspace-wide, so a caller whose active catalog is `open` gets
    it too; without the cause named, that reads as an unexplained regression.
    """
    if not scoped_slugs:
        return _ENUMERATION_HINT
    named = ", ".join(f"`{slug}`" for slug in scoped_slugs)
    noun = "Catalog" if len(scoped_slugs) == 1 else "Catalogs"
    verb = "is" if len(scoped_slugs) == 1 else "are"
    return (
        f"{_ENUMERATION_HINT} {noun} {named} {verb} attached in scoped mode, which "
        "disables engine-side enumeration for every catalog in this workspace."
    )


def metadata_enumeration_reason(sql: str, scoped_slugs: list[str] | None = None) -> str | None:
    """Why ``sql`` reaches catalog metadata in a way grants cannot check.

    ``information_schema`` and its siblings are computed by DuckDB across every
    attached catalog, so a scoped principal sees objects they hold no grant on —
    the browse endpoints filter their listings (``visible_schemas`` /
    ``visible_tables``) but the engine has no way to. Since the rows cannot be
    filtered, the statement is rejected instead, and callers are pointed at the
    REST browse endpoints that *can* filter.

    Consulted whenever **any** catalog attached to the workspace is scoped (see
    :func:`assert_query_access`), not only when the statement names that catalog:
    the listings span every attachment at once, so they would expose the scoped
    catalog's objects from a session whose active catalog is open. Workspaces with
    no scoped attachment — the default — never reach this and keep today's
    behavior. ``DESCRIBE`` is deliberately not covered: it names its object, so it
    is grant-checked per object at ``metadata`` tier like any other ref.

    ``scoped_slugs``, when given, names those catalogs in the returned reason.

    Returns a user-facing reason, or ``None`` when nothing enumerates.
    """
    hint = _enumeration_hint(scoped_slugs)
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as exc:  # sqlglot.errors.ParseError et al - fail closed
        raise GrantDenied(f"Could not parse SQL for grant check: {exc}") from exc

    for stmt in statements:
        if stmt is None:
            continue
        for table in stmt.find_all(exp.Table):
            if table.db and table.db.lower() in METADATA_SCHEMAS:
                # Matched on the schema, so this catches both the bare
                # `information_schema.tables` and dbt-duckdb's
                # `system.information_schema.tables` spelling.
                return f"`{table.db}.{table.name}` cannot be queried. {hint}"
        for fn in stmt.find_all(exp.Anonymous):
            if isinstance(fn.this, str) and fn.this.lower() in _CATALOG_META_FUNCTIONS:
                return f"`{fn.this}()` cannot be called. {hint}"
        if isinstance(stmt, exp.Show):
            return f"`SHOW` cannot be used. {hint}"
        if isinstance(stmt, exp.Pragma):
            node = stmt.this
            if isinstance(node, exp.Anonymous) and isinstance(node.this, str):
                name = node.this.lower()
            elif isinstance(node, exp.Column):
                name = node.name.lower()
            else:
                name = ""
            if name in _UNCHECKABLE_PRAGMAS:
                return f"`PRAGMA {name}` cannot be used. {hint}"
    return None


def is_exempt_ref(catalog: str | None, schema: str | None) -> bool:
    """True for the built-in discovery surfaces (system catalog / info schema)."""
    return (catalog in SYSTEM_CATALOGS) or (schema in METADATA_SCHEMAS)


async def assert_query_access(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    principal_id: uuid.UUID | None,
    sql: str,
    active_catalog: str,
    catalogs: list[Catalog],
) -> None:
    """Reject a query before dispatch if the principal lacks tier on any object.

    Every referenced table needs at least ``reader`` (``writer`` for the write
    target, ``metadata`` for a ``DESCRIBE``) on any catalog whose attachment is
    scoped, and statements that enumerate catalog objects the engine cannot filter
    by grant are rejected outright (:func:`metadata_enumeration_reason`) — the
    latter for the whole workspace once *any* attachment is scoped, including
    sessions whose active catalog is open. Runs for
    both interactive and scheduled dispatch — the shared chokepoint. It is a
    **no-op** when no attached catalog is scoped, so open workspaces never even
    parse the SQL and keep today's behavior byte-for-byte.

    Raises :class:`GrantDenied` (a ``ValueError``) on any shortfall, an
    unresolvable/unattached catalog reference, an unparseable statement, or a
    missing principal — all fail-closed.
    """
    # Import here to avoid a module-level cycle with the workspace service.
    from api.services.workspace import DEFAULT_SCHEMA

    scoped_ids = {c.id for c in catalogs if await is_scoped(db, workspace_id, c)}
    if not scoped_ids:
        return

    # Unfilterable engine-side enumeration is rejected outright once any attached
    # catalog is scoped: DuckDB computes those listings across every attachment
    # and cannot narrow them to the principal's grants. Deliberately workspace-
    # wide, not per referenced catalog — the agent multi-ATTACHes every catalog
    # the workspace binds, so these listings expose the scoped catalog's objects
    # even from a session whose active catalog is open. The reason names the
    # scoped catalogs so that denial is explicable from an open one.
    scoped_slugs = sorted(c.slug for c in catalogs if c.id in scoped_ids)
    if (reason := metadata_enumeration_reason(sql, scoped_slugs)) is not None:
        raise GrantDenied(reason)

    by_slug = {c.slug: c for c in catalogs}
    for ref in extract_table_refs(sql):
        if is_exempt_ref(ref.catalog, ref.schema):
            continue
        cat = by_slug.get(ref.catalog or active_catalog)
        if cat is None:
            raise GrantDenied(f"Query references unknown catalog '{ref.catalog or active_catalog}'")
        if cat.id not in scoped_ids:
            continue  # open catalog — unrestricted
        if principal_id is None:
            raise GrantDenied("A scoped catalog requires an authenticated principal")
        schema = ref.schema or DEFAULT_SCHEMA
        if ref.is_target:
            need = "writer"
        elif ref.is_metadata_only:
            need = "metadata"
        else:
            need = "reader"
        tier = await node_tier(db, workspace_id, cat, principal_id, schema, ref.table)
        if tier_rank(tier) < TIER_SCALE[need]:
            raise GrantDenied(f"Not authorized ({need}) on {cat.slug}.{schema}.{ref.table}")
