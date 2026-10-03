"""Serving and admitting cached query results.

**Lookup** runs before a query is dispatched. The query is parsed and its tables
resolved (`eligibility`), every table's current version is asked of its catalog,
and the entry for the query's identity (`key`) is loaded. The entry is served
only while each table it was computed from is unchanged — or changed solely by
commits that rewrote files without changing data, in which case the entry's
versions are brought forward. A hit becomes an ordinary, already-finished query
row that points at the run whose result it reuses; nothing is dispatched.

**Admission** runs when a missed query finishes. The versions recorded before
dispatch are read again, and the result is kept only if not one table committed
in between: otherwise the rows could mix two versions of a table, or be keyed
under a version they were not computed from. Small results are copied into
Postgres; larger ones stay where the agent wrote them.

Lookup fails open and admission fails closed. Any doubt during lookup — a
catalog that does not answer, a budget run out — means the query simply runs,
exactly as it would without a cache. Any doubt during admission means the result
is not kept.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.metrics import (
    record_result_cache_admission,
    record_result_cache_eviction,
    record_result_cache_lookup,
    record_result_cache_revalidation,
)
from api.models.agent import Agent
from api.models.catalog import Catalog
from api.models.query import Query
from api.models.result_cache import ResultCacheEntry
from api.models.workspace import Workspace
from api.services.catalog_backends import CatalogBackendError, TableVersion, backend_for
from api.services.result_cache.eligibility import Ineligible, TableRef, analyze, resolve
from api.services.result_cache.key import AttachedCatalog, CacheContext, cache_key

logger = logging.getLogger(__name__)

# Origins whose runs may use the cache: interactive runs (null, or `elastic` for
# one sent to the pool), schedules and session statements. The rest are internal
# (`sample`, `metadata`, `maintenance*`, `export`) and must always really run.
CACHEABLE_ORIGINS = frozenset({None, "elastic", "scheduled", "session"})

HIT, MISS, BYPASS, INELIGIBLE = "hit", "miss", "bypass", "ineligible"

# The session factory admission opens its own sessions from. Admission runs after
# the frame that triggered it has been handled, so it cannot borrow that session;
# tests point this at their own engine.
admission_session_factory: async_sessionmaker[AsyncSession] | None = None
_admissions: set[asyncio.Task] = set()


@dataclass(frozen=True)
class Dependency:
    """One thing a result was computed from: a table, or (``table`` None) the
    functions a catalog stores, which can shadow built-ins."""

    catalog_id: str
    catalog: str
    schema: str | None
    table: str | None
    content_id: str
    version_token: str

    @property
    def ident(self) -> tuple[str, str | None, str | None]:
        return (self.catalog_id, self.schema, self.table)

    @property
    def version(self) -> TableVersion:
        return TableVersion(content_id=self.content_id, version_token=self.version_token)


@dataclass
class Lookup:
    """What the cache decided about one query, and what a miss needs later."""

    status: str
    detail: str | None = None
    key_hash: str | None = None
    context: CacheContext | None = None
    deps: list[Dependency] = field(default_factory=list)
    entry: ResultCacheEntry | None = None
    elapsed_s: float = 0.0

    @property
    def hit(self) -> bool:
        return self.status == HIT

    def stamp(self, query: Query) -> None:
        """Record the decision on a query row that is about to be dispatched."""
        query.cache_status = self.status
        query.cache_detail = self.detail
        if self.status == MISS and self.context is not None:
            query.cache_key_hash = self.key_hash
            query.cache_versions = {
                "context": asdict(self.context),
                "deps": [asdict(d) for d in self.deps],
            }


def _bypass(detail: str) -> Lookup:
    return Lookup(status=BYPASS, detail=detail)


def enabled_for(workspace: Workspace, *, use_cache: bool) -> str | None:
    """None when the cache may be used, else why not (a `bypass` detail)."""
    if not settings.result_cache_enabled:
        return "disabled"
    if not workspace.result_cache_enabled:
        return "workspace_disabled"
    if not use_cache:
        return "opted_out"
    return None


def context_for(agent: Agent | None, *, current_catalog: str, current_schema: str) -> CacheContext:
    """The context a run on ``agent`` would execute in; with no agent chosen yet,
    the deployment default runtime and the assumed time zone. Getting it wrong
    only costs a miss: an entry carries the context it was really computed in."""
    from api.services import runtimes as runtime_service

    runtime = runtime_service.runtime_id_of(agent) if agent is not None else None
    timezone = (agent.capabilities or {}).get("timezone") if agent is not None else None
    return CacheContext(
        current_catalog=current_catalog,
        current_schema=current_schema,
        runtime_id=runtime or settings.default_runtime,
        timezone=timezone or settings.result_cache_assumed_timezone,
    )


def _attached(catalogs: list[Catalog]) -> list[AttachedCatalog]:
    return [
        AttachedCatalog(
            catalog_id=str(c.id),
            slug=c.slug,
            kind=c.kind,
            storage_backend_id=str(c.storage_backend_id) if c.storage_backend_id else None,
        )
        for c in catalogs
    ]


async def resolve_dependencies(
    refs: list[TableRef],
    catalogs: list[Catalog],
    *,
    current_catalog: str,
    polaris: Any,
) -> list[Dependency]:
    """Every table's current version, one batched request per catalog, plus the
    current catalog's stored-functions version when its kind has any.

    Raises :class:`Ineligible` (`not_a_table`) when a required relation is not a
    table — a view, or a name that does not exist (the run will fail on its own).
    """
    by_slug = {c.slug: c for c in catalogs}
    wanted: dict[str, list[TableRef]] = {}
    for ref in refs:
        wanted.setdefault(ref.catalog, []).append(ref)

    async def one_catalog(slug: str, catalog_refs: list[TableRef]) -> list[Dependency]:
        catalog = by_slug[slug]
        backend = backend_for(catalog, polaris=polaris)
        found = await backend.table_versions(catalog, [(r.schema, r.table) for r in catalog_refs])
        deps = []
        for ref in catalog_refs:
            version = found.get((ref.schema, ref.table))
            if version is None:
                if ref.optional:
                    continue
                raise Ineligible("not_a_table")
            deps.append(
                Dependency(
                    str(catalog.id),
                    slug,
                    ref.schema,
                    ref.table,
                    version.content_id,
                    version.version_token,
                )
            )
        return deps

    async def routines() -> list[Dependency]:
        catalog = by_slug[current_catalog]
        version = await backend_for(catalog, polaris=polaris).routines_version(catalog)
        if version is None:
            return []
        return [Dependency(str(catalog.id), current_catalog, None, None, version, version)]

    groups = await asyncio.gather(
        *(one_catalog(slug, group) for slug, group in wanted.items()), routines()
    )
    deps = [d for group in groups for d in group]
    return sorted(deps, key=lambda d: (d.catalog_id, d.schema or "", d.table or ""))


async def _still_valid(
    stored: list[dict[str, Any]],
    current: list[Dependency],
    catalogs: list[Catalog],
    polaris: Any,
) -> str:
    """`valid`, `revalidated` (only compaction-style commits since) or `stale`."""
    before = {(d["catalog_id"], d["schema"], d["table"]): d for d in stored}
    now = {d.ident: d for d in current}
    if before.keys() != now.keys():
        # A CTE-shadowed name started or stopped existing as a table.
        return "stale"
    changed = [d for ident, d in now.items() if before[ident]["content_id"] != d.content_id]
    if not changed:
        return "valid"
    by_id = {str(c.id): c for c in catalogs}
    for dep in changed:
        if dep.table is None:
            return "stale"  # a stored function changed
        catalog = by_id[dep.catalog_id]
        old = before[dep.ident]
        equivalent = await backend_for(catalog, polaris=polaris).data_equivalent(
            catalog,
            dep.schema or "",
            dep.table,
            TableVersion(old["content_id"], old["version_token"]),
            dep.version,
        )
        if not equivalent:
            return "stale"
        record_result_cache_revalidation(catalog.kind)
    return "revalidated"


async def _agent_result_available(db: AsyncSession, entry: ResultCacheEntry) -> bool:
    """Whether the agent still holds the entry's result file."""
    from api.services.agent_dispatch import is_agent_connected
    from api.services.query import agent_session_token

    if entry.agent_id is None or not await is_agent_connected(db, entry.agent_id):
        return False
    agent = await db.get(Agent, entry.agent_id)
    if agent is None or agent.result_host is None or agent.result_port is None:
        return False
    token = await agent_session_token(db, agent.id)
    url = f"http://{agent.result_host}:{agent.result_port}/results/{entry.source_query_id}.parquet"
    try:
        async with httpx.AsyncClient(timeout=settings.result_cache_lookup_timeout_s) as client:
            resp = await client.head(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        return False
    return resp.status_code == 200


async def _drop(db: AsyncSession, entry: ResultCacheEntry, reason: str) -> None:
    from api.services.result_cache.storage import release

    await release(db, [entry])
    await db.delete(entry)
    await db.commit()
    record_result_cache_eviction(reason)


async def lookup(
    db: AsyncSession,
    *,
    workspace: Workspace,
    catalogs: list[Catalog],
    sql: str,
    origin: str | None,
    use_cache: bool,
    context: CacheContext,
    polaris: Any,
) -> Lookup:
    """Decide whether ``sql`` can be answered from the cache right now."""
    started = time.perf_counter()
    result = await _lookup(
        db,
        workspace=workspace,
        catalogs=catalogs,
        sql=sql,
        origin=origin,
        use_cache=use_cache,
        context=context,
        polaris=polaris,
    )
    result.elapsed_s = time.perf_counter() - started
    record_result_cache_lookup(result.status, result.detail, result.elapsed_s)
    return result


async def _lookup(
    db: AsyncSession,
    *,
    workspace: Workspace,
    catalogs: list[Catalog],
    sql: str,
    origin: str | None,
    use_cache: bool,
    context: CacheContext,
    polaris: Any,
) -> Lookup:
    if (off := enabled_for(workspace, use_cache=use_cache)) is not None:
        return _bypass(off)
    if origin not in CACHEABLE_ORIGINS:
        return Lookup(status=INELIGIBLE, detail="origin")
    try:
        analysis = analyze(sql)
        refs = resolve(
            analysis,
            attached={c.slug for c in catalogs},
            current_catalog=context.current_catalog,
            current_schema=context.current_schema,
        )
        # Only the catalog round trips run under the budget: cancelling them is
        # harmless, cancelling an operation on `db` would not be.
        deps = await asyncio.wait_for(
            resolve_dependencies(
                refs, catalogs, current_catalog=context.current_catalog, polaris=polaris
            ),
            timeout=settings.result_cache_lookup_timeout_s,
        )
    except Ineligible as exc:
        return Lookup(status=INELIGIBLE, detail=exc.reason)
    except TimeoutError:
        return _bypass("lookup_timeout")
    except CatalogBackendError:
        return _bypass("version_unavailable")

    key_hash = cache_key(
        workspace_id=workspace.id,
        canonical=analysis.canonical,
        context=context,
        attached=_attached(catalogs),
    )
    miss = Lookup(status=MISS, key_hash=key_hash, context=context, deps=deps)
    entry = await db.scalar(
        sa.select(ResultCacheEntry).where(
            ResultCacheEntry.workspace_id == workspace.id,
            ResultCacheEntry.key_hash == key_hash,
        )
    )
    if entry is None:
        return miss

    now = datetime.now(tz=UTC)
    if _aware(entry.expires_at) <= now or _aware(entry.hard_expires_at) <= now:
        await _drop(db, entry, "expired")
        return miss
    try:
        validity = await asyncio.wait_for(
            _still_valid(entry.tables, deps, catalogs, polaris),
            timeout=settings.result_cache_lookup_timeout_s,
        )
    except TimeoutError, CatalogBackendError:
        return Lookup(status=BYPASS, detail="lookup_timeout")
    if validity == "stale":
        await _drop(db, entry, "stale")
        miss.detail = "stale"
        return miss
    if entry.storage == "agent" and not await _agent_result_available(db, entry):
        await _drop(db, entry, "gone")
        miss.detail = "result_gone"
        return miss
    if validity == "revalidated":
        entry.tables = [asdict(d) for d in deps]
    return Lookup(status=HIT, key_hash=key_hash, context=context, deps=deps, entry=entry)


def _aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; Postgres aware ones."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def serve_hit(
    db: AsyncSession,
    result: Lookup,
    query: Query,
    *,
    principal_id: uuid.UUID | None,
    catalogs: list[Catalog],
    polaris: Any,
) -> Query:
    """Turn ``query`` (unsaved) into a finished row answered by the cache.

    The caller's grant check is repeated here so that no path can serve a cached
    result to someone who could not have run the query: the entry is shared by
    the workspace, the right to read its tables is not.
    """
    from api.services import grants as grant_service
    from api.services.query import _record_lineage

    entry = result.entry
    assert entry is not None and result.context is not None
    await grant_service.assert_query_access(
        db,
        query.workspace_id,
        principal_id,
        query.sql,
        result.context.current_catalog,
        catalogs,
    )
    now = datetime.now(tz=UTC)
    query.status = "done"
    query.agent_id = None
    query.cache_status = HIT
    query.cache_detail = None
    query.cache_key_hash = result.key_hash
    query.result_source_query_id = entry.source_query_id
    query.runtime_id = entry.runtime_id
    query.row_count = entry.row_count
    query.result_bytes = entry.result_bytes
    query.result_schema = entry.result_schema
    query.result_path = f"cache:{entry.source_query_id}"
    query.duration_ms = int(result.elapsed_s * 1000)
    query.running_at = now
    query.finished_at = now
    db.add(query)
    entry.hit_count += 1
    entry.last_hit_at = now
    entry.expires_at = min(
        now + timedelta(hours=settings.result_cache_ttl_hours), _aware(entry.hard_expires_at)
    )
    await db.commit()
    await _record_lineage(db, query, polaris)
    return query


# --- Admission ---------------------------------------------------------------


def schedule_admission(query_id: uuid.UUID, polaris: Any) -> None:
    """Admit a finished miss in the background, off the agent's frame loop."""
    factory = admission_session_factory
    if factory is None:
        from api.db.session import async_session_factory

        factory = async_session_factory

    async def _run() -> None:
        try:
            async with factory() as db:
                await admit(db, query_id, polaris)
        except Exception:
            logger.exception("Result cache admission failed for query %s", query_id)

    task = asyncio.get_running_loop().create_task(_run())
    _admissions.add(task)
    task.add_done_callback(_admissions.discard)


async def drain_admissions() -> None:
    """Wait for in-flight admissions (shutdown, and tests)."""
    while _admissions:
        await asyncio.gather(*list(_admissions), return_exceptions=True)


async def admit(db: AsyncSession, query_id: uuid.UUID, polaris: Any) -> str | None:
    """Keep a finished miss's result if nothing it read moved while it ran.

    Returns the storage used, or None when the result was not kept (the reason
    lands on the query row as ``cache_detail``).
    """
    query = await db.get(Query, query_id)
    if query is None or query.cache_status != MISS or query.status != "done":
        return None
    reason = await _admission_refusal(db, query, polaris)
    if reason is not None:
        query.cache_detail = reason
        await db.commit()
        record_result_cache_admission("refused", reason)
        return None
    from api.services.result_cache.storage import store

    storage = await store(db, query)
    if storage is None:
        query.cache_detail = "result_unreadable"
        await db.commit()
        record_result_cache_admission("refused", "result_unreadable")
        return None
    record_result_cache_admission("admitted", storage)
    return storage


async def _admission_refusal(db: AsyncSession, query: Query, polaris: Any) -> str | None:
    if query.result_path is None or query.agent_id is None or query.row_count is None:
        return "no_result"
    recorded = query.cache_versions or {}
    context = recorded.get("context") or {}
    agent = await db.get(Agent, query.agent_id)
    if agent is None:
        return "agent_gone"
    # The run must really have happened in the context it was keyed under: an
    # elastic run is keyed before its agent exists.
    from api.services import runtimes as runtime_service

    timezone = (agent.capabilities or {}).get("timezone")
    if timezone is None:
        return "unknown_timezone"
    if runtime_service.runtime_id_of(agent) != context.get("runtime_id") or timezone != context.get(
        "timezone"
    ):
        return "context_mismatch"

    deps = [Dependency(**d) for d in recorded.get("deps") or []]
    from api.services.workspace import resolve_workspace_catalogs

    catalogs = await resolve_workspace_catalogs(db, query.workspace_id)
    by_id = {str(c.id): c for c in catalogs}
    if any(d.catalog_id not in by_id for d in deps):
        return "catalog_detached"
    refs = [
        TableRef(by_id[d.catalog_id].slug, d.schema, d.table)
        for d in deps
        if d.table is not None and d.schema is not None
    ]
    try:
        now = await asyncio.wait_for(
            resolve_dependencies(
                refs,
                catalogs,
                current_catalog=context.get("current_catalog", ""),
                polaris=polaris,
            ),
            timeout=settings.result_cache_lookup_timeout_s,
        )
    except Ineligible, TimeoutError, CatalogBackendError, KeyError:
        return "version_unavailable"
    if [(d.ident, d.version_token) for d in now] != [(d.ident, d.version_token) for d in deps]:
        return "changed_during_run"
    return None
