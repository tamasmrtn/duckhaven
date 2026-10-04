"""Where a cached result's rows live, and how much of that space each one gets.

A result small enough (``RESULT_CACHE_INLINE_MAX_BYTES``) is copied into the
entry, so serving it needs nothing but Postgres: no running agent, no cold start
for an elastic pool scaled to zero. A larger one stays in the Parquet file the
agent wrote, and the agent is told to keep that file past its normal retention
for as long as the entry exists.

Inline space is bounded per workspace and in total. When it runs out, the entries
least worth keeping go first, judged the way WATCHMAN (VLDB '96) does: what a hit
saves (the compute it took to produce) times how often it is hit, per byte it
occupies. An entry younger than ``RESULT_CACHE_MIN_LEASE_S`` is never evicted for
space — it has not yet had the chance to be hit (Nectar's lease, OSDI '10).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.metrics import record_result_cache_eviction
from api.models.agent import Agent
from api.models.query import Query
from api.models.result_cache import ResultCacheEntry

logger = logging.getLogger(__name__)

INLINE, AGENT = "inline", "agent"


async def store(db: AsyncSession, query: Query) -> str | None:
    """Create the entry for a finished miss; the storage used, or None when the
    result could not be read back (or another replica stored it first)."""
    from api.services.query import agent_session_token, proxy_rows

    agent = await db.get(Agent, query.agent_id)
    if agent is None:
        return None
    inline: bytes | None = None
    storage = AGENT
    if (query.result_bytes or 0) <= settings.result_cache_inline_max_bytes:
        if agent.result_host is None or agent.result_port is None:
            return None
        token = await agent_session_token(db, agent.id)
        try:
            resp = await proxy_rows(agent, query, token=token)
        except httpx.HTTPError:
            return None
        if resp.status_code != 200:
            return None
        inline = resp.content
        storage = INLINE

    recorded = query.cache_versions or {}
    existing = await db.scalar(
        sa.select(ResultCacheEntry).where(
            ResultCacheEntry.workspace_id == query.workspace_id,
            ResultCacheEntry.key_hash == query.cache_key_hash,
        )
    )
    if existing is not None:
        await release(db, [existing])
        await db.delete(existing)
        await db.flush()
        record_result_cache_eviction("replaced")
    now = datetime.now(tz=UTC)
    entry = ResultCacheEntry(
        workspace_id=query.workspace_id,
        key_hash=query.cache_key_hash,
        sql=query.sql,
        context=recorded.get("context") or {},
        tables=recorded.get("deps") or [],
        source_query_id=query.id,
        storage=storage,
        agent_id=agent.id if storage == AGENT else None,
        inline_parquet=inline,
        result_bytes=len(inline) if inline is not None else (query.result_bytes or 0),
        row_count=query.row_count or 0,
        result_schema=query.result_schema,
        runtime_id=query.runtime_id,
        compute_ms=query.duration_ms or 0,
        hit_count=0,
        created_at=now,
        expires_at=now + timedelta(hours=settings.result_cache_ttl_hours),
        hard_expires_at=now + timedelta(hours=settings.result_cache_max_age_hours),
    )
    db.add(entry)
    try:
        await db.commit()
    except IntegrityError:
        # Another replica admitted the same query a moment earlier; its entry is
        # as good as this one would have been.
        await db.rollback()
        return None
    if storage == AGENT:
        await retain(db, entry)
    else:
        await enforce_inline_quota(db, query.workspace_id)
    return storage


async def retain(db: AsyncSession, entry: ResultCacheEntry) -> None:
    """Ask the agent to keep an entry's result file until the entry expires.

    Best-effort: if the agent cannot be told, its normal retention sweep removes
    the file in time and the next lookup finds it gone and drops the entry.
    """
    from api.services.agent_dispatch import send_to_agent
    from duckhaven_shared.protocol import Frame, FrameType

    if entry.agent_id is None:
        return
    frame = Frame(
        type=FrameType.RETAIN_RESULT,
        payload={
            "query_id": str(entry.source_query_id),
            "retain_until": _aware(entry.hard_expires_at).timestamp(),
        },
    )
    try:
        await send_to_agent(db, entry.agent_id, frame.model_dump_json())
    except Exception:  # noqa: BLE001 - best-effort, see docstring
        logger.warning("Could not ask agent %s to retain a cached result", entry.agent_id)


async def release(db: AsyncSession, entries: list[ResultCacheEntry]) -> None:
    """Tell agents they may delete the result files of entries going away."""
    from api.services.agent_dispatch import send_to_agent
    from duckhaven_shared.protocol import Frame, FrameType

    for entry in entries:
        if entry.storage != AGENT or entry.agent_id is None:
            continue
        frame = Frame(
            type=FrameType.RELEASE_RESULT, payload={"query_id": str(entry.source_query_id)}
        )
        try:
            await send_to_agent(db, entry.agent_id, frame.model_dump_json())
        except Exception:  # noqa: BLE001 - the agent's own sweep is the backstop
            logger.warning("Could not tell agent %s to release a cached result", entry.agent_id)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _profit(
    *, compute_ms: int, hit_count: int, result_bytes: int, created_at: datetime, now: datetime
) -> float:
    """What keeping an entry saves per byte per second: WATCHMAN's λ·c/s, with the
    reference rate estimated as hits (plus the run that created it) over age."""
    age_s = max((now - _aware(created_at)).total_seconds(), 1.0)
    return max(compute_ms, 1) * (hit_count + 1) / age_s / max(result_bytes, 1)


async def enforce_inline_quota(db: AsyncSession, workspace_id: uuid.UUID | None) -> int:
    """Evict inline entries until the workspace and total budgets hold; returns how
    many were evicted. Only sizes and stats are read, never the stored bytes."""
    now = datetime.now(tz=UTC)
    rows = (
        await db.execute(
            sa.select(
                ResultCacheEntry.id,
                ResultCacheEntry.workspace_id,
                ResultCacheEntry.result_bytes,
                ResultCacheEntry.compute_ms,
                ResultCacheEntry.hit_count,
                ResultCacheEntry.created_at,
            ).where(ResultCacheEntry.storage == INLINE)
        )
    ).all()
    lease = timedelta(seconds=settings.result_cache_min_lease_s)
    candidates = sorted(
        (r for r in rows if now - _aware(r.created_at) >= lease),
        key=lambda r: _profit(
            compute_ms=r.compute_ms,
            hit_count=r.hit_count,
            result_bytes=r.result_bytes,
            created_at=r.created_at,
            now=now,
        ),
    )
    victims: set[uuid.UUID] = set()
    if workspace_id is not None:
        used = sum(r.result_bytes for r in rows if r.workspace_id == workspace_id)
        for r in candidates:
            if used <= settings.result_cache_inline_workspace_max_bytes:
                break
            if r.workspace_id == workspace_id:
                victims.add(r.id)
                used -= r.result_bytes
    total = sum(r.result_bytes for r in rows if r.id not in victims)
    for r in candidates:
        if total <= settings.result_cache_inline_total_max_bytes:
            break
        if r.id not in victims:
            victims.add(r.id)
            total -= r.result_bytes
    if victims:
        await db.execute(sa.delete(ResultCacheEntry).where(ResultCacheEntry.id.in_(victims)))
        await db.commit()
        record_result_cache_eviction("space", len(victims))
    return len(victims)
