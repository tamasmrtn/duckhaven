"""Reconstructing lineage from query history.

The properties worth pinning down are the ones a careless implementation gets
wrong quietly: that a replayed statement keeps its original observation time,
that a second pass changes nothing, that a rehearsal writes nothing, and that one
unreadable statement does not end the walk.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.models.lineage import LineageEdge
from api.models.lineage_backfill import LineageBackfill
from api.models.query import Query
from api.services.lineage import backfill
from api.services.lineage.backfill import (
    BackfillInProgress,
    advance,
    request_backfill,
)
from api.services.lineage.times import aware_utc

CTAS = "CREATE TABLE warehouse.analytics.dim AS SELECT * FROM raw.analytics.src"
OTHER_CTAS = "CREATE TABLE warehouse.analytics.rollup AS SELECT * FROM raw.analytics.events"

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


async def _history(env, *, sql: str, ran_at: datetime, status: str = "done") -> Query:
    """One statement in the workspace's history, as it would have been recorded."""
    query = Query(
        workspace_id=env["workspace"].id,
        user_id=env["user"].id,
        sql=sql,
        status=status,
        started_at=ran_at,
        finished_at=ran_at + timedelta(seconds=2),
        active_catalog="warehouse",
    )
    env["db"].add(query)
    await env["db"].flush()
    return query


async def _edges(db) -> list[LineageEdge]:
    return list((await db.execute(sa.select(LineageEdge))).scalars().all())


async def _run(env, *, since=None, dry_run=False, batch_size=100) -> LineageBackfill:
    """Request a backfill and drain it, the way the runner loop would."""
    db = env["db"]
    row = await request_backfill(
        db,
        workspace_id=env["workspace"].id,
        since=since,
        dry_run=dry_run,
        requested_by=env["user"].id,
    )
    for _ in range(50):  # a generous bound; the walk is tiny in these tests
        await advance(db, row, batch_size=batch_size)
        if row.status not in ("pending", "running"):
            break
    return row


# --- reconstruction ---------------------------------------------------------


async def test_history_produces_lineage(graph_env):
    db = graph_env["db"]
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=90))

    row = await _run(graph_env)

    assert row.status == "completed"
    assert (row.queries_scanned, row.queries_with_lineage, row.edges_created) == (1, 1, 1)
    (edge,) = await _edges(db)
    assert (edge.source_table, edge.target_table) == ("src", "dim")
    assert edge.provider == "execution"


async def test_a_replayed_statement_keeps_the_time_it_actually_ran(graph_env):
    """The interaction that would otherwise make backfill destroy freshness.

    A transformation last run in February must land in the graph as observed in
    February. Stamping it with the time the backfill happened to run would make
    every historical relationship look confirmed today.
    """
    db = graph_env["db"]
    long_ago = NOW - timedelta(days=180)
    query = await _history(graph_env, sql=CTAS, ran_at=long_ago)

    await _run(graph_env)

    (edge,) = await _edges(db)
    assert edge.last_seen_at.replace(tzinfo=UTC) == query.finished_at
    assert edge.first_seen_at.replace(tzinfo=UTC) == query.finished_at
    # And therefore, six months on, plainly not something anyone just confirmed.
    assert edge.last_seen_at.replace(tzinfo=UTC) < datetime.now(tz=UTC) - timedelta(days=90)


async def test_ordering_does_not_change_the_result(graph_env):
    """Chronological processing is not load-bearing, and this is why it is safe
    not to depend on it: the write path takes the earliest first-seen, the latest
    last-seen, and the newest observation's description."""
    db = graph_env["db"]
    early, late = NOW - timedelta(days=100), NOW - timedelta(days=10)
    first = await _history(graph_env, sql=CTAS, ran_at=early)
    last = await _history(graph_env, sql=CTAS, ran_at=late)

    # Forward.
    await _run(graph_env)
    (forward,) = await _edges(db)
    seen = (forward.first_seen_at, forward.last_seen_at, forward.last_query_id)

    await db.execute(sa.delete(LineageEdge))
    await db.execute(sa.delete(LineageBackfill))
    await db.flush()

    # Backwards: same rows, walked newest-first by hand.
    from api.services.lineage.ingest import record_execution_lineage

    for query in (last, first):
        await record_execution_lineage(db, query)
    (backward,) = await _edges(db)

    assert (backward.first_seen_at, backward.last_seen_at, backward.last_query_id) == seen
    assert backward.last_query_id == last.id


# --- idempotency ------------------------------------------------------------


async def test_running_it_twice_changes_nothing(graph_env):
    db = graph_env["db"]
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=90))

    await _run(graph_env)
    (before,) = await _edges(db)
    counted, first_seen, last_seen = (
        before.observation_count,
        before.first_seen_at,
        before.last_seen_at,
    )

    second = await _run(graph_env)

    assert second.status == "completed"
    assert second.queries_scanned == 0, "history already read should not be read again"
    (after,) = await _edges(db)
    assert after.observation_count == counted
    assert (after.first_seen_at, after.last_seen_at) == (first_seen, last_seen)
    assert len(await _edges(db)) == 1


async def test_a_deeper_request_reads_only_the_part_not_yet_covered(graph_env):
    db = graph_env["db"]
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=10))
    await _history(graph_env, sql=OTHER_CTAS, ran_at=NOW - timedelta(days=200))

    shallow = await _run(graph_env, since=NOW - timedelta(days=30))
    assert shallow.queries_scanned == 1

    deep = await _run(graph_env, since=NOW - timedelta(days=365))
    assert deep.queries_scanned == 1, "the already-covered recent window is not re-read"
    assert {e.target_table for e in await _edges(db)} == {"dim", "rollup"}


# --- bounds -----------------------------------------------------------------


async def test_a_bounded_window_leaves_older_history_alone(graph_env):
    db = graph_env["db"]
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=5))
    await _history(graph_env, sql=OTHER_CTAS, ran_at=NOW - timedelta(days=400))

    row = await _run(graph_env, since=NOW - timedelta(days=30))

    assert row.queries_scanned == 1
    assert {e.target_table for e in await _edges(db)} == {"dim"}


async def test_empty_history_completes(graph_env):
    row = await _run(graph_env)
    assert row.status == "completed"
    assert row.queries_scanned == 0
    assert await _edges(graph_env["db"]) == []


async def test_only_completed_statements_are_replayed(graph_env):
    """A run that failed may have written nothing. Asserting a relationship on
    the strength of SQL that did not finish is exactly the confidently-wrong
    metadata the graph is supposed to avoid."""
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=3), status="failed")

    row = await _run(graph_env)

    assert row.queries_scanned == 0
    assert await _edges(graph_env["db"]) == []


# --- resilience -------------------------------------------------------------


async def test_one_unparseable_statement_does_not_end_the_walk(graph_env):
    db = graph_env["db"]
    await _history(graph_env, sql="selct * from nowhere", ran_at=NOW - timedelta(days=9))
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=8))

    row = await _run(graph_env)

    assert row.status == "completed"
    assert row.queries_scanned == 2
    assert row.parse_failures == 1
    assert row.queries_with_lineage == 1
    assert len(await _edges(db)) == 1


async def test_a_statement_that_establishes_nothing_is_counted_as_skipped(graph_env):
    await _history(graph_env, sql="SELECT * FROM raw.analytics.src", ran_at=NOW)

    row = await _run(graph_env)

    assert (row.queries_scanned, row.queries_skipped, row.queries_with_lineage) == (1, 1, 0)


# --- batching and resumption ------------------------------------------------


async def test_a_history_larger_than_one_batch_resumes_from_the_cursor(graph_env):
    db = graph_env["db"]
    for day in range(5):
        await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=100 + day))

    row = await request_backfill(
        db,
        workspace_id=graph_env["workspace"].id,
        since=None,
        dry_run=False,
        requested_by=graph_env["user"].id,
    )
    await advance(db, row, batch_size=2)
    assert (row.status, row.queries_scanned) == ("running", 2)
    cursor = row.cursor_query_id

    await advance(db, row, batch_size=2)
    assert row.queries_scanned == 4
    assert row.cursor_query_id != cursor

    await advance(db, row, batch_size=2)
    assert (row.status, row.queries_scanned) == ("completed", 5)
    # Five runs of the same statement: one relationship, observed five times.
    (edge,) = await _edges(db)
    assert edge.observation_count == 5


async def test_a_cancel_stops_the_walk_and_keeps_what_it_found(graph_env):
    db = graph_env["db"]
    for day in range(4):
        await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=50 + day))

    row = await request_backfill(
        db,
        workspace_id=graph_env["workspace"].id,
        since=None,
        dry_run=False,
        requested_by=graph_env["user"].id,
    )
    await advance(db, row, batch_size=2)
    row.cancel_requested = True
    await advance(db, row, batch_size=2)

    assert row.status == "cancelled"
    assert row.queries_scanned == 2
    # What it derived before stopping is real lineage and stays.
    assert len(await _edges(db)) == 1
    # But the coverage window did not move, so a later run reads the rest.
    assert row.covered_from is None


# --- dry run ----------------------------------------------------------------


async def test_a_dry_run_reports_what_it_would_do_and_writes_nothing(graph_env):
    db = graph_env["db"]
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=30))
    await _history(graph_env, sql=OTHER_CTAS, ran_at=NOW - timedelta(days=20))

    row = await _run(graph_env, dry_run=True)

    assert row.status == "completed"
    assert (row.queries_scanned, row.queries_with_lineage, row.edges_created) == (2, 2, 2)
    assert await _edges(db) == [], "a rehearsal must leave the graph untouched"


async def test_a_dry_run_does_not_claim_the_history_it_rehearsed(graph_env):
    """Otherwise the real pass that follows would skip exactly the range the
    rehearsal was about, and quietly do nothing."""
    db = graph_env["db"]
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=30))

    rehearsal = await _run(graph_env, dry_run=True)
    assert rehearsal.covered_from is None

    real = await _run(graph_env)
    assert real.queries_scanned == 1
    assert len(await _edges(db)) == 1


async def test_a_dry_run_over_existing_lineage_leaves_its_counts_alone(graph_env):
    db = graph_env["db"]
    query = await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=30))
    from api.services.lineage.ingest import record_execution_lineage

    await record_execution_lineage(db, query)
    (before,) = await _edges(db)
    counted, last_seen = before.observation_count, before.last_seen_at

    # Force the rehearsal to look at history the live path already covered.
    row = await request_backfill(
        db,
        workspace_id=graph_env["workspace"].id,
        since=None,
        dry_run=True,
        requested_by=graph_env["user"].id,
    )
    row.covered_through = datetime.now(tz=UTC)
    await advance(db, row, batch_size=100)

    (after,) = await _edges(db)
    assert (after.observation_count, after.last_seen_at) == (counted, last_seen)


# --- request handling -------------------------------------------------------


async def test_a_second_request_while_one_is_running_is_refused(graph_env):
    db = graph_env["db"]
    await request_backfill(
        db,
        workspace_id=graph_env["workspace"].id,
        since=None,
        dry_run=False,
        requested_by=graph_env["user"].id,
    )
    with pytest.raises(BackfillInProgress):
        await request_backfill(
            db,
            workspace_id=graph_env["workspace"].id,
            since=None,
            dry_run=False,
            requested_by=graph_env["user"].id,
        )


async def test_a_re_request_resets_the_counters_but_keeps_the_coverage(graph_env):
    db = graph_env["db"]
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=30))
    first = await _run(graph_env)
    covered = first.covered_from
    assert covered is not None

    row = await request_backfill(
        db,
        workspace_id=graph_env["workspace"].id,
        since=NOW - timedelta(days=365),
        dry_run=False,
        requested_by=graph_env["user"].id,
    )
    assert row.queries_scanned == 0
    assert row.covered_from == covered


async def test_a_workspace_with_no_catalogs_completes_without_reading_history(graph_env):
    """Nothing a historical statement names could resolve, so there is nothing
    to read — and the run should say so rather than churn through the history."""
    from api.models.catalog import WorkspaceCatalog

    db = graph_env["db"]
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=30))
    await db.execute(
        sa.delete(WorkspaceCatalog).where(
            WorkspaceCatalog.workspace_id == graph_env["workspace"].id
        )
    )
    await db.flush()

    row = await _run(graph_env)

    assert (row.status, row.queries_scanned) == ("completed", 0)


async def test_history_from_another_workspace_is_not_read(graph_env):
    from api.models.workspace import Workspace

    db = graph_env["db"]
    other = Workspace(slug=f"other-{uuid.uuid4().hex[:6]}", name="Other")
    db.add(other)
    await db.flush()
    db.add(
        Query(
            workspace_id=other.id,
            sql=CTAS,
            status="done",
            started_at=NOW - timedelta(days=30),
            finished_at=NOW - timedelta(days=30),
            active_catalog="warehouse",
        )
    )
    await db.flush()

    row = await _run(graph_env)

    assert row.queries_scanned == 0
    assert await _edges(db) == []


async def test_an_unreadable_statement_is_counted_and_stepped_over(graph_env, monkeypatch):
    """Not just an unparseable one. Anything at all going wrong on a single
    statement must cost that statement and nothing more, or a workspace with one
    awkward row in its history can never complete a backfill."""
    db = graph_env["db"]
    boom = await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=9))
    await _history(graph_env, sql=OTHER_CTAS, ran_at=NOW - timedelta(days=8))

    real = backfill.record_execution_lineage

    async def explode(session, query, **kwargs):
        if query.id == boom.id:
            raise RuntimeError("something unexpected")
        return await real(session, query, **kwargs)

    monkeypatch.setattr(backfill, "record_execution_lineage", explode)

    row = await _run(graph_env)

    assert row.status == "completed"
    assert (row.queries_scanned, row.queries_failed, row.queries_with_lineage) == (2, 1, 1)
    assert {e.target_table for e in await _edges(db)} == {"rollup"}


async def test_the_sql_of_a_failing_statement_is_never_logged(graph_env, monkeypatch, caplog):
    """The statement id is the pointer to its text, and `queries` is where that
    text is readable by whoever is entitled to it. A log line is not."""
    secret = "CREATE TABLE warehouse.analytics.dim AS SELECT 'hunter2' AS pw FROM raw.analytics.src"
    await _history(graph_env, sql=secret, ran_at=NOW - timedelta(days=9))

    async def explode(session, query, **kwargs):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(backfill, "record_execution_lineage", explode)

    with caplog.at_level(logging.DEBUG):
        await _run(graph_env)

    assert "hunter2" not in caplog.text
    assert "CREATE TABLE" not in caplog.text


# --- the runner cycle -------------------------------------------------------


async def test_a_cycle_with_nothing_outstanding_is_idle(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    assert (await backfill.run_cycle(factory))["status"] == "idle"


async def test_a_cycle_advances_an_outstanding_backfill(graph_env, db_engine):
    db = graph_env["db"]
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=30))
    await request_backfill(
        db,
        workspace_id=graph_env["workspace"].id,
        since=None,
        dry_run=False,
        requested_by=graph_env["user"].id,
    )
    await db.commit()

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    result = await backfill.run_cycle(factory)

    assert result["status"] == "ran"
    async with factory() as check:
        row = (await check.execute(sa.select(LineageBackfill))).scalar_one()
        assert row.status == "completed"
        assert row.queries_with_lineage == 1


async def test_a_failing_run_is_recorded_on_the_row_rather_than_raised(
    graph_env, db_engine, monkeypatch
):
    """Lineage is metadata. A backfill that cannot proceed has to fail visibly
    and leave the loop — and everything else — running."""
    db = graph_env["db"]
    await request_backfill(
        db,
        workspace_id=graph_env["workspace"].id,
        since=None,
        dry_run=False,
        requested_by=graph_env["user"].id,
    )
    await db.commit()

    async def explode(*_args, **_kwargs):
        raise RuntimeError("the walk cannot proceed")

    monkeypatch.setattr(backfill, "advance", explode)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    result = await backfill.run_cycle(factory)

    assert result == {"status": "ran", "failed": True}
    async with factory() as check:
        row = (await check.execute(sa.select(LineageBackfill))).scalar_one()
        assert row.status == "failed"
        assert "the walk cannot proceed" in row.error
        assert row.finished_at is not None


async def test_a_tick_runs_a_cycle_when_this_replica_leads(db_engine):
    """SQLite has no advisory locks, so leadership is always granted there — the
    point of the test is that the tick delegates rather than short-circuits."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    assert (await backfill.run_tick(factory))["status"] == "idle"


# --- failure isolation under a real transaction -----------------------------


async def test_a_statement_that_fails_mid_write_does_not_poison_the_batch(graph_env, monkeypatch):
    """Catching the exception is not enough on its own.

    A database-level failure aborts the surrounding transaction, so without a
    per-statement savepoint the half-written state would still be there and every
    *later* statement in the batch would fail too — one bad row reported as four
    hundred casualties. The savepoint is what makes the recovery real.
    """
    db = graph_env["db"]
    boom = await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=9))
    await _history(graph_env, sql=OTHER_CTAS, ran_at=NOW - timedelta(days=8))

    real = backfill.record_execution_lineage

    async def half_written(session, query, **kwargs):
        result = await real(session, query, **kwargs)
        if query.id == boom.id:
            raise RuntimeError("failed after writing")
        return result

    monkeypatch.setattr(backfill, "record_execution_lineage", half_written)

    row = await _run(graph_env)

    assert row.status == "completed"
    assert (row.queries_scanned, row.queries_failed) == (2, 1)
    # The statement after the failure was processed normally...
    assert row.queries_with_lineage == 1
    # ...and the failed one left nothing behind.
    assert {e.target_table for e in await _edges(db)} == {"rollup"}


async def test_a_commit_failure_is_recorded_on_the_row(graph_env, db_engine, monkeypatch):
    """Otherwise it escapes past the handler that would mark the run failed, the
    row stays `running` with its cursor unmoved, and the next tick replays the
    identical batch forever with nothing to say why."""
    db = graph_env["db"]
    workspace_id = graph_env["workspace"].id
    await request_backfill(
        db, workspace_id=workspace_id, since=None, dry_run=False, requested_by=None
    )
    await db.commit()

    async def advance_then_break(session, row, **_kwargs):
        # Leave the session holding a write that cannot commit: a second state
        # row for a workspace that already has one.
        session.add(LineageBackfill(workspace_id=workspace_id))
        return {"backfill": str(row.id), "backfill_status": row.status, "scanned": 1}

    monkeypatch.setattr(backfill, "advance", advance_then_break)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    result = await backfill.run_cycle(factory)

    assert result == {"status": "ran", "failed": True}
    async with factory() as check:
        row = (await check.execute(sa.select(LineageBackfill))).scalar_one()
        assert row.status == "failed"
        assert row.error


# --- coverage bookkeeping ---------------------------------------------------


async def test_a_run_with_no_catalogs_records_no_coverage(graph_env):
    """It read nothing, so it must not claim to have. Recording the window would
    lock the workspace out of ever backfilling once a catalog is attached."""
    from api.models.catalog import WorkspaceCatalog

    db = graph_env["db"]
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=30))
    detached = await db.execute(
        sa.delete(WorkspaceCatalog).where(
            WorkspaceCatalog.workspace_id == graph_env["workspace"].id
        )
    )
    assert detached.rowcount
    await db.flush()

    row = await _run(graph_env)
    assert (row.status, row.covered_from) == ("completed", None)


async def test_another_workspaces_lineage_does_not_shrink_this_ones_window(graph_env):
    """Edges are catalog-scoped facts shared across workspaces, so asking the
    catalog when recording started would answer for whoever started earliest. A
    workspace created against a long-lived catalog would then be told its whole
    history was covered and would silently backfill nothing."""
    from api.models.workspace import Workspace
    from api.services.lineage.ingest import CanonicalEdge, upsert_edges
    from api.services.lineage.keys import internal_ref

    db = graph_env["db"]
    older = Workspace(slug=f"older-{uuid.uuid4().hex[:6]}", name="Older")
    db.add(older)
    await db.flush()
    # The other workspace has been recording lineage in a shared catalog for ages.
    await upsert_edges(
        db,
        [
            CanonicalEdge(
                source=internal_ref(graph_env["catalogs"]["raw"].id, "analytics", "src"),
                target=internal_ref(graph_env["catalogs"]["warehouse"].id, "analytics", "old"),
            )
        ],
        provider="execution",
        workspace_id=older.id,
        observed_at=NOW - timedelta(days=500),
    )
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=30))

    row = await _run(graph_env)

    assert row.queries_scanned == 1, "this workspace's own history is still readable"
    assert {e.target_table for e in await _edges(db)} == {"old", "dim"}


# --- resuming ---------------------------------------------------------------


async def test_resuming_a_cancelled_run_does_not_recount_what_it_already_read(graph_env):
    """Coverage is only recorded on completion, so a cancelled walk's only record
    of how far it got is the cursor. Discarding it would replay everything the
    run had already read and add a second observation to each relationship."""
    db = graph_env["db"]
    for day in range(4):
        await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=50 + day))

    row = await request_backfill(
        db,
        workspace_id=graph_env["workspace"].id,
        since=None,
        dry_run=False,
        requested_by=None,
    )
    await advance(db, row, batch_size=2)
    row.cancel_requested = True
    await advance(db, row, batch_size=2)
    assert row.status == "cancelled"

    resumed = await _run(graph_env)

    assert resumed.status == "completed"
    assert resumed.queries_scanned == 2, "only the two it had not reached"
    # Four statements, four observations — not six.
    (edge,) = await _edges(db)
    assert edge.observation_count == 4


async def test_asking_for_a_different_window_starts_a_fresh_walk(graph_env):
    db = graph_env["db"]
    for day in range(4):
        await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=50 + day))

    row = await request_backfill(
        db,
        workspace_id=graph_env["workspace"].id,
        since=None,
        dry_run=False,
        requested_by=None,
    )
    await advance(db, row, batch_size=2)
    row.cancel_requested = True
    await advance(db, row, batch_size=2)

    restarted = await request_backfill(
        db,
        workspace_id=graph_env["workspace"].id,
        since=NOW - timedelta(days=60),
        dry_run=False,
        requested_by=None,
    )
    assert restarted.cursor_query_id is None


async def test_a_re_request_reports_the_time_it_was_asked_for(graph_env):
    db = graph_env["db"]
    first = await _run(graph_env)
    original = first.created_at

    again = await request_backfill(
        db,
        workspace_id=graph_env["workspace"].id,
        since=None,
        dry_run=False,
        requested_by=None,
    )

    assert aware_utc(again.created_at) > aware_utc(original)


# --- what gets read ---------------------------------------------------------


async def test_synthetic_reads_are_excluded_without_being_paged_through(graph_env):
    """They fire on every click in the catalog explorer, so on a busy workspace
    they are most of what `queries` holds. Filtering them in SQL keeps the walk
    off rows it would only throw away — and keeps `queries_skipped` meaning
    something."""
    db = graph_env["db"]
    for origin in ("sample", "metadata"):
        noise = await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=20))
        noise.origin = origin
    await db.flush()
    await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=10))

    row = await _run(graph_env)

    assert row.queries_scanned == 1, "the synthetic rows never reached the walk"
    assert row.queries_with_lineage == 1


async def test_an_interactive_query_is_still_read(graph_env):
    """`origin` is NULL for every interactive query, and `NOT IN` against NULL is
    NULL — so a bare `notin_` would filter out precisely the statements the walk
    exists to find. This is that regression."""
    db = graph_env["db"]
    query = await _history(graph_env, sql=CTAS, ran_at=NOW - timedelta(days=10))
    assert query.origin is None

    row = await _run(graph_env)

    assert (row.queries_scanned, row.queries_with_lineage) == (1, 1)
    assert len(await _edges(db)) == 1
