"""End-to-end WAL + ledger resumability — the exact property the plan's
Phase 0 gate requires validating before any real Azure spend: "deliberately
interrupt mid-run, re-invoke, confirm no duplicate/re-billed work."

This simulates a run of 3 queries where the process dies after the 2nd, then
a fresh process resumes from the WAL + ledger alone.
"""

from __future__ import annotations

from tpch_bench.ledger.store import Ledger, work_item_id
from tpch_bench.ledger.wal import WalWriter, read_events

QUERY_IDS = ["q01", "q02", "q03"]


def _work_items():
    return [
        work_item_id(
            kind="query",
            engine="duckhaven",
            scale_factor=1,
            scenario="sequential",
            query_id=q,
            rep=1,
        )
        for q in QUERY_IDS
    ]


def _run_and_record(
    ledger: Ledger, wal: WalWriter, wid: str, query_id: str, duration_ms: float
) -> None:
    """What the (not-yet-built) orchestrator does per query: WAL first, then
    ledger — matching plan §5's "every result is appended [to the WAL]
    before any DuckDB write"."""
    wal.write(
        "work_items",
        {
            "work_item_id": wid,
            "kind": "query",
            "engine": "duckhaven",
            "scale_factor": 1,
            "scenario": "sequential",
            "query_id": query_id,
            "rep": 1,
            "status": "running",
            "attempt": 1,
        },
    )
    ledger.mark_running(wid)

    wal.write("query_results", {"work_item_id": wid, "server_duration_ms": duration_ms})
    ledger.record_query_result(work_item_id=wid, server_duration_ms=duration_ms)

    wal.write(
        "work_items",
        {
            "work_item_id": wid,
            "kind": "query",
            "engine": "duckhaven",
            "scale_factor": 1,
            "scenario": "sequential",
            "query_id": query_id,
            "rep": 1,
            "status": "done",
            "attempt": 1,
        },
    )
    ledger.mark_done(wid)


def test_a_crash_mid_run_loses_nothing_and_reruns_nothing_twice(tmp_path):
    db_path = tmp_path / "results.duckdb"
    wal_path = tmp_path / "wal.jsonl"
    ids = _work_items()

    # ── Session 1: register all 3, complete 2, then "crash" ────────────
    with Ledger(db_path) as ledger, WalWriter(wal_path) as wal:
        for wid, q in zip(ids, QUERY_IDS, strict=True):
            ledger.register_work_item(
                work_item_id=wid,
                kind="query",
                engine="duckhaven",
                scale_factor=1,
                scenario="sequential",
                query_id=q,
                rep=1,
            )
        _run_and_record(ledger, wal, ids[0], "q01", 100.0)
        _run_and_record(ledger, wal, ids[1], "q02", 200.0)
        # Process "dies" here — q03 never runs.

    # ── Session 2: fresh process, same paths — this is the resumed run ──
    with Ledger(db_path) as ledger:
        # A resumed run always replays the WAL first, in case the ledger
        # file itself is stale relative to it (plan §5).
        applied = ledger.upsert_from_wal(read_events(wal_path))
        assert applied > 0

        # Re-register the full expected set, exactly as a fresh invocation
        # would — this must not disturb what's already done.
        for wid, q in zip(ids, QUERY_IDS, strict=True):
            ledger.register_work_item(
                work_item_id=wid,
                kind="query",
                engine="duckhaven",
                scale_factor=1,
                scenario="sequential",
                query_id=q,
                rep=1,
            )

        remaining = ledger.pending_work_item_ids(ids)
        assert remaining == [ids[2]], "only q03 should be owed — q01/q02 are already done"

        # Finish the one query that was actually interrupted.
        with WalWriter(wal_path) as wal:
            _run_and_record(ledger, wal, ids[2], "q03", 300.0)

    # ── Verify: exactly 3 results, none duplicated, q01/q02 kept their
    #    original timing rather than being silently rerun ───────────────
    with Ledger(db_path) as ledger:
        assert ledger.pending_work_item_ids(ids) == []
        rows = ledger.conn.execute(
            "SELECT work_item_id, server_duration_ms FROM query_results ORDER BY work_item_id"
        ).fetchall()
        assert len(rows) == 3
        by_id = dict(rows)
        assert by_id[ids[0]] == 100.0
        assert by_id[ids[1]] == 200.0
        assert by_id[ids[2]] == 300.0
