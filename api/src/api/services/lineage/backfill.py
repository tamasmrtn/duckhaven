"""Reconstruct lineage from query history that predates lineage.

DuckHaven retains every statement's SQL text indefinitely, so a graph that starts
empty is empty only because nothing has read the history yet. A team six months
into using DuckHaven should get six months of lineage on the day they upgrade,
not three weeks of watching it fill in.

**The whole point is that this is not a second implementation.** A historical
statement goes through :func:`~api.services.lineage.ingest.record_execution_lineage`,
the identical function the agent frame handler calls the moment a query finishes.
There is one parser, one set of resolution rules and one write path; the only
thing this module contributes is orchestration — which rows to feed it, in what
batches, and how to stop and start again.

Two properties are what make it safe to run in production, and both are worth
stating because neither is obvious:

**A backfilled relationship is not a fresh one.** Each statement is replayed with
``observed_at`` set to when it actually ran, so a transformation last executed in
February lands with a February timestamp and is immediately, correctly, stale.
Getting this wrong would have made backfill quietly destroy the freshness signal
it shares a release with.

**Running it twice does nothing the second time.** The relationships themselves
are naturally idempotent — identity is ``(provider, source, target)`` under a
unique constraint — but the observation *counts* are not, so re-reading the same
history would inflate them. ``covered_from``/``covered_through`` on the state row
record which history has already been read, and a second request scans only
what is genuinely uncovered.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.models.lineage import LineageEdge
from api.models.lineage_backfill import ACTIVE_STATUSES, LineageBackfill
from api.models.query import Query
from api.services.lineage.ingest import (
    EXECUTION_PROVIDER,
    INTERNAL_ORIGINS,
    WorkspaceCatalogs,
    record_execution_lineage,
    workspace_catalog_context,
)
from api.services.lineage.times import aware_utc as _aware

logger = logging.getLogger(__name__)

# Cluster-wide advisory-lock key electing the single backfill-runner leader each
# tick. Arbitrary constant ('dhbl'); only its uniqueness against the other
# advisory locks matters (scheduler 0x64687371, scanner 0x64687363, migration
# 0x6468636D, session reaper 0x64687373, telemetry 0x64687374).
_BACKFILL_LOCK_KEY = 0x6468626C

# Only completed statements. A failed or cancelled run may have written nothing,
# and asserting a relationship on the strength of SQL that did not finish is
# exactly the confidently-wrong metadata lineage is supposed to avoid.
_ELIGIBLE_STATUS = "done"


async def request_backfill(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    since: datetime | None,
    dry_run: bool,
    requested_by: uuid.UUID | None,
) -> LineageBackfill:
    """Queue (or re-queue) a workspace's backfill, returning its state row.

    Raises :class:`BackfillInProgress` if one is already running — a second walk
    over the same history would be pure duplicated work, and the two would race
    over the cursor.
    """
    row = (
        await db.execute(
            sa.select(LineageBackfill).where(LineageBackfill.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()

    if row is not None and row.status in ACTIVE_STATUSES:
        raise BackfillInProgress(row)

    if row is None:
        row = LineageBackfill(workspace_id=workspace_id)
        db.add(row)

    # An interrupted run over the same window is resumed rather than restarted.
    # Coverage is only recorded on completion, so a cancelled walk leaves no
    # trace of how far it got except the cursor — and discarding that would make
    # the next request re-read everything it already read, adding a second
    # observation to every relationship it had found. Any change to the window
    # asks for a different walk, so the cursor cannot carry over.
    resuming = (
        not row.dry_run
        and not dry_run
        and row.status in ("cancelled", "failed")
        and row.since_at == since
        and row.cursor_query_id is not None
    )

    row.status = "pending"
    row.dry_run = dry_run
    row.since_at = since
    row.requested_by = requested_by
    # The time of *this* request, which is what the API reports and what the
    # runner orders outstanding work by.
    row.created_at = datetime.now(tz=UTC)
    row.cancel_requested = False
    row.error = None
    row.started_at = None
    row.finished_at = None
    # Counters describe the *current* run, so a re-request starts them over. The
    # coverage window is what carries across runs, and is left alone.
    if not resuming:
        row.cursor_started_at = None
        row.cursor_query_id = None
    row.queries_scanned = 0
    row.queries_with_lineage = 0
    row.queries_skipped = 0
    row.parse_failures = 0
    row.queries_failed = 0
    row.edges_created = 0
    row.edges_updated = 0
    try:
        await db.flush()
    except IntegrityError as exc:
        # Two requests raced past the check above and both tried to insert. The
        # unique constraint is the real arbiter; the loser is in exactly the
        # situation the check exists to report, so it reports it rather than
        # surfacing a constraint violation as a 500.
        await db.rollback()
        raise BackfillInProgress(None) from exc
    return row


class BackfillInProgress(Exception):
    """A backfill is already running for this workspace."""

    def __init__(self, row: LineageBackfill | None = None) -> None:
        super().__init__("A lineage backfill is already running for this workspace")
        self.row = row


async def _live_from(db: AsyncSession, workspace_id: uuid.UUID) -> datetime | None:
    """When execution-derived lineage started being recorded *for this workspace*.

    Everything after this point was already observed as it happened, so replaying
    it would count the same observation twice. Before it, this workspace's
    statements went unwatched.

    Scoped by ``workspace_id`` — the column that records where an edge was
    observed — rather than by catalog, because edges are catalog-scoped facts
    shared between every workspace that attaches the catalog. Asking the catalog
    would answer "when did *anyone* start recording here", so a workspace created
    last month against a catalog in use since January would be told its entire
    history was already covered, and would backfill nothing at all while
    reporting success.

    Derived from the data rather than configured, because the honest answer is
    "whenever this workspace first ran a statement with lineage switched on",
    which nothing else records. One aggregate per run, not per statement.
    """
    return (
        await db.execute(
            sa.select(sa.func.min(LineageEdge.first_seen_at)).where(
                LineageEdge.provider == EXECUTION_PROVIDER,
                LineageEdge.workspace_id == workspace_id,
            )
        )
    ).scalar()


async def _begin(db: AsyncSession, row: LineageBackfill) -> None:
    """Settle the window this run will walk, then mark it running.

    The upper bound is where live extraction takes over. The lower bound is as
    far back as asked, and the run stops early at ``covered_from`` because
    everything from there up has already been read.

    Settled once and kept: later runs reuse it, which matters because backfilling
    moves ``first_seen_at`` backwards, so recomputing it afterwards would read a
    boundary this process itself created.
    """
    now = datetime.now(tz=UTC)
    if row.covered_through is None:
        live_from = await _live_from(db, row.workspace_id)
        row.covered_through = now if live_from is None else min(now, _aware(live_from))

    row.status = "running"
    row.started_at = now
    await db.flush()


def _window(row: LineageBackfill) -> tuple[datetime | None, datetime | None]:
    """The half-open ``[floor, ceiling)`` of history this run still has to read.

    ``covered_from`` is the previous runs' reach; anything newer than it has been
    read already, so a run asked to go further back reads only the older part.
    """
    floor = _aware(row.since_at) if row.since_at is not None else None
    ceiling = _aware(row.covered_through) if row.covered_through is not None else None
    if row.covered_from is not None:
        already = _aware(row.covered_from)
        ceiling = already if ceiling is None else min(ceiling, already)
    return floor, ceiling


async def _next_batch(db: AsyncSession, row: LineageBackfill, *, limit: int) -> list[Query]:
    """The next page of eligible history, in resumable order."""
    floor, ceiling = _window(row)
    stmt = sa.select(Query).where(
        Query.workspace_id == row.workspace_id,
        Query.status == _ELIGIBLE_STATUS,
        # DuckHaven's own synthetic reads, excluded here as well as inside the
        # extraction that would discard them anyway. They fire on every click in
        # the catalog explorer, so on a busy workspace they are most of what
        # `queries` holds — paging through them would spend most of the walk on
        # rows destined to be thrown away, and would bury the real numbers under
        # a `queries_skipped` in the tens of thousands.
        #
        # The NULL arm is load-bearing: `origin` is NULL for every interactive
        # query, and `NOT IN` against NULL is NULL, so a bare `notin_` would
        # filter out precisely the statements this exists to find.
        sa.or_(Query.origin.is_(None), Query.origin.notin_(INTERNAL_ORIGINS)),
    )
    if floor is not None:
        stmt = stmt.where(Query.started_at >= floor)
    if ceiling is not None:
        stmt = stmt.where(Query.started_at < ceiling)
    if row.cursor_started_at is not None:
        # "Strictly after this exact row", spelled out rather than as a row-value
        # comparison so it means the same thing on both backends. Without the id
        # tiebreak a batch boundary landing inside a burst of statements sharing
        # a timestamp would either re-read or skip the rest of the burst.
        at = _aware(row.cursor_started_at)
        stmt = stmt.where(
            sa.or_(
                Query.started_at > at,
                sa.and_(Query.started_at == at, Query.id > row.cursor_query_id),
            )
        )
    stmt = stmt.order_by(Query.started_at, Query.id).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


@dataclass
class _Tally:
    """What one batch found, held apart from the state row.

    A dry run replays inside a savepoint that is then rolled back, and the state
    row lives in the same session — so counting into it directly would roll the
    counts back along with the writes, and the rehearsal would report nothing.
    """

    scanned: int = 0
    with_lineage: int = 0
    skipped: int = 0
    parse_failures: int = 0
    failed: int = 0
    created: int = 0
    updated: int = 0


def _apply(row: LineageBackfill, tally: _Tally) -> None:
    row.queries_scanned += tally.scanned
    row.queries_with_lineage += tally.with_lineage
    row.queries_skipped += tally.skipped
    row.parse_failures += tally.parse_failures
    row.queries_failed += tally.failed
    row.edges_created += tally.created
    row.edges_updated += tally.updated


async def _replay(db: AsyncSession, batch: list[Query], context: WorkspaceCatalogs) -> _Tally:
    """Feed one batch through the live extraction path, tallying as it goes.

    One statement's failure is one statement's failure. Historical SQL is a
    grab-bag — dialect drift, control commands recorded for audit, statements
    written against catalogs that no longer exist — and aborting on the first of
    them would mean nobody ever gets a complete backfill.

    Each statement gets its own savepoint, which is what makes that true for the
    failures most likely to happen. Catching the exception is not enough on its
    own: a database-level failure — the live extraction path inserting the same
    edge a moment earlier, a lost connection — aborts the surrounding
    transaction, so without a savepoint to roll back to, every *later* statement
    in the batch would fail too and the batch would be reported as one bad
    statement followed by four hundred casualties.
    """
    tally = _Tally()
    for query in batch:
        tally.scanned += 1
        savepoint = await db.begin_nested()
        try:
            result = await record_execution_lineage(db, query, context=context)
            await savepoint.commit()
        except Exception:  # noqa: BLE001 - one bad statement must not end the walk
            await savepoint.rollback()
            # The statement id, never its text: the SQL is in `queries` for
            # anyone entitled to read it, and a log line is not that place.
            logger.exception("Lineage backfill failed on query %s", query.id)
            tally.failed += 1
            continue

        if result.parse_failed:
            tally.parse_failures += 1
        elif result.created or result.updated:
            tally.with_lineage += 1
            tally.created += result.created
            tally.updated += result.updated
        else:
            # Parsed fine and established nothing — a read, an INSERT ... VALUES,
            # a CREATE with only a column list. The common case, and not a
            # problem.
            tally.skipped += 1
    return tally


# "All available history" as a concrete lower bound, so `covered_from` can record
# "we have read everything" without a second nullable flag meaning the same thing.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _finish(row: LineageBackfill, *, status: str, covered: bool = True) -> None:
    """End a run, recording the history it read unless it did not read any.

    ``covered=False`` is for a run that finished without actually walking the
    window — the only honest thing to do then is leave the coverage ledger alone,
    since recording a range as read is what stops it ever being read again.
    """
    row.status = status
    row.finished_at = datetime.now(tz=UTC)
    if status == "completed" and covered:
        # The window just read joins everything read before it. `covered_from`
        # only ever moves backwards, which is what makes a repeat request a
        # no-op and a deeper request read only the new depth.
        #
        # A dry run deliberately does not record coverage: it wrote nothing, so
        # claiming the history as read would make the real run that follows skip
        # exactly the range it was rehearsing.
        if row.dry_run:
            return
        floor, _ = _window(row)
        reach = floor if floor is not None else _EPOCH
        row.covered_from = (
            reach if row.covered_from is None else min(_aware(row.covered_from), reach)
        )


async def advance(db: AsyncSession, row: LineageBackfill, *, batch_size: int) -> dict[str, Any]:
    """Walk one batch of a claimed backfill and persist where it got to.

    Returns a small summary for the runner's log — keyed ``backfill_status`` so
    it can be merged into the cycle's own result without the two ``status``
    values silently overwriting each other. The caller commits: one batch is the
    unit of durable progress, so an interruption anywhere costs at most one batch
    of re-reading.
    """
    context = await workspace_catalog_context(db, row.workspace_id)
    if row.status == "pending":
        await _begin(db, row)

    if row.cancel_requested:
        _finish(row, status="cancelled")
        return {"backfill": str(row.id), "backfill_status": row.status, "scanned": 0}

    if not context.ids:
        # No catalogs attached: nothing any historical statement names could
        # resolve to, so there is no history worth reading *right now*. Finished
        # without recording coverage, because it read nothing — claiming the
        # history as read would permanently lock the workspace out of ever
        # backfilling it once a catalog is attached.
        _finish(row, status="completed", covered=False)
        return {"backfill": str(row.id), "backfill_status": row.status, "scanned": 0}

    batch = await _next_batch(db, row, limit=batch_size)
    if not batch:
        _finish(row, status="completed")
        return {"backfill": str(row.id), "backfill_status": row.status, "scanned": 0}

    # Captured before any rollback, since the rows themselves are expired by one.
    cursor_at, cursor_id = batch[-1].started_at, batch[-1].id

    if row.dry_run:
        # A rehearsal that exercises the real thing: the same extraction, the
        # same upserts, against the same rows — then rolled back, so the only
        # trace left is the count of what would have changed. Anything cheaper
        # would be a second implementation reporting on the first.
        savepoint = await db.begin_nested()
        try:
            tally = await _replay(db, batch, context)
        finally:
            await savepoint.rollback()
        # The rollback expired everything in the session, including the state
        # row; reload it explicitly rather than letting a lazy refresh fire from
        # attribute access.
        await db.refresh(row)
    else:
        tally = await _replay(db, batch, context)

    _apply(row, tally)
    row.cursor_started_at = cursor_at
    row.cursor_query_id = cursor_id
    if len(batch) < batch_size:
        _finish(row, status="completed")
    return {"backfill": str(row.id), "backfill_status": row.status, "scanned": len(batch)}


async def run_cycle(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """Advance the oldest outstanding backfill by one batch."""
    async with session_factory() as db:
        row = (
            await db.execute(
                sa.select(LineageBackfill)
                .where(LineageBackfill.status.in_(ACTIVE_STATUSES))
                .order_by(LineageBackfill.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return {"status": "idle"}

        # Held separately: a rollback expires the instance, and the id is what
        # the failure path needs to reload it.
        row_id = row.id
        try:
            summary = await advance(db, row, batch_size=settings.lineage_backfill_batch_size)
            # Inside the `try` on purpose. A commit can fail on its own, and a
            # failure that escaped here would leave the row `running` with its
            # cursor unmoved — so the next tick would re-select it and replay the
            # identical batch, forever, with nothing on the row to say why.
            await db.commit()
        except Exception as exc:  # noqa: BLE001 - a run must fail, not the loop
            await db.rollback()
            failed = await db.get(LineageBackfill, row_id)
            if failed is not None:
                failed.status = "failed"
                failed.error = f"{type(exc).__name__}: {exc}"
                failed.finished_at = datetime.now(tz=UTC)
                await db.commit()
            logger.exception("Lineage backfill failed")
            return {"status": "ran", "failed": True}

        return {"status": "ran", **summary}


@contextlib.asynccontextmanager
async def backfill_leadership(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[bool]:
    """Yield ``True`` iff this replica holds the cluster-wide backfill lock.

    Mirrors the scheduler's leadership: a Postgres session-level advisory lock
    ensures exactly one replica runs a cycle per tick. On backends without
    advisory locks (SQLite under tests) leadership is always granted.
    """
    async with session_factory() as db:
        if db.bind.dialect.name != "postgresql":
            yield True
            return
        got = bool(
            (
                await db.execute(
                    sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": _BACKFILL_LOCK_KEY}
                )
            ).scalar()
        )
        try:
            yield got
        finally:
            if got:
                await db.execute(
                    sa.text("SELECT pg_advisory_unlock(:k)"), {"k": _BACKFILL_LOCK_KEY}
                )
                await db.commit()


async def run_tick(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """One runner tick: run a cycle only if this replica wins leadership."""
    async with backfill_leadership(session_factory) as is_leader:
        if not is_leader:
            return {"status": "standby"}
        return await run_cycle(session_factory)


async def backfill_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Background loop: drain requested lineage backfills a batch at a time.

    Deliberately off the query path. Lineage is metadata — reconstructing it must
    never be able to slow down or fail the work it describes — so the loop walks
    history at its own pace, one committed batch per tick, and an operator can
    make it slower or faster with the batch size alone.
    """
    logger.info(
        "Lineage backfill runner started (tick %.0fs, batch %d)",
        settings.lineage_backfill_tick_s,
        settings.lineage_backfill_batch_size,
    )
    while True:
        try:
            result = await run_tick(session_factory)
            if result.get("status") == "ran":
                logger.info("Lineage backfill cycle: %s", result)
        except Exception as exc:  # noqa: BLE001 - the loop must survive any cycle failure
            logger.exception("Lineage backfill cycle failed: %s", exc)
        await asyncio.sleep(settings.lineage_backfill_tick_s)
