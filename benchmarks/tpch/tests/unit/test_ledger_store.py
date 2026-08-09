"""The ledger is what makes a multi-day, real-money benchmark campaign safe
to interrupt and resume: every test here is really asking "if the process
died right here, would a second run redo (and re-bill) this work?"
"""

from __future__ import annotations

import pytest

from tpch_bench.ledger.store import Ledger, work_item_id


@pytest.fixture
def ledger():
    with Ledger(":memory:") as led:
        yield led


def test_work_item_id_is_deterministic():
    a = work_item_id(
        kind="query",
        engine="duckhaven",
        scale_factor=100,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    b = work_item_id(
        kind="query",
        engine="duckhaven",
        scale_factor=100,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    assert a == b


def test_work_item_id_distinguishes_every_axis():
    base = dict(
        kind="query",
        engine="duckhaven",
        scale_factor=100,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    ids = {
        work_item_id(**base),
        work_item_id(**{**base, "engine": "snowflake"}),
        work_item_id(**{**base, "scale_factor": 300}),
        work_item_id(**{**base, "scenario": "cold_start"}),
        work_item_id(**{**base, "query_id": "q02"}),
        work_item_id(**{**base, "rep": 2}),
    }
    assert len(ids) == 6, "every distinct axis must hash to a distinct id"


def test_register_work_item_starts_pending(ledger):
    wid = work_item_id(
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    ledger.register_work_item(
        work_item_id=wid,
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    assert ledger.status(wid) == "pending"
    assert not ledger.is_done(wid)


def test_status_is_none_for_an_unregistered_item(ledger):
    assert ledger.status("does-not-exist") is None


def test_re_registering_does_not_reset_progress(ledger):
    """This is the property that makes resumption safe: the orchestrator
    recomputes the full expected work-item set every run and re-registers
    all of it, which must never clobber work already marked done."""
    wid = work_item_id(
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    kwargs = dict(
        work_item_id=wid,
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    ledger.register_work_item(**kwargs)
    ledger.mark_running(wid)
    ledger.mark_done(wid)

    ledger.register_work_item(**kwargs)  # the "resumed run recomputes everything" call

    assert ledger.status(wid) == "done"


def test_mark_running_then_done_transitions(ledger):
    wid = work_item_id(
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    ledger.register_work_item(
        work_item_id=wid,
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    ledger.mark_running(wid)
    assert ledger.status(wid) == "running"
    ledger.mark_done(wid)
    assert ledger.status(wid) == "done"


def test_mark_failed_leaves_a_failed_status(ledger):
    wid = work_item_id(
        kind="query",
        engine="snowflake",
        scale_factor=1000,
        scenario="sequential",
        query_id="q05",
        rep=1,
    )
    ledger.register_work_item(
        work_item_id=wid,
        kind="query",
        engine="snowflake",
        scale_factor=1000,
        scenario="sequential",
        query_id="q05",
        rep=1,
    )
    ledger.mark_running(wid)
    ledger.mark_failed(wid)
    assert ledger.status(wid) == "failed"


def test_mark_running_increments_attempt_across_retries(ledger):
    wid = work_item_id(
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    ledger.register_work_item(
        work_item_id=wid,
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    ledger.mark_running(wid)
    ledger.mark_failed(wid)
    ledger.mark_running(wid)  # retry
    attempt = ledger.conn.execute(
        "SELECT attempt FROM work_items WHERE work_item_id = ?", [wid]
    ).fetchone()[0]
    assert attempt == 2


def test_mark_running_on_an_unregistered_item_raises(ledger):
    with pytest.raises(KeyError):
        ledger.mark_running("never-registered")


def test_pending_work_item_ids_excludes_only_done(ledger):
    ids = []
    for q in ["q01", "q02", "q03"]:
        wid = work_item_id(
            kind="query",
            engine="duckhaven",
            scale_factor=1,
            scenario="sequential",
            query_id=q,
            rep=1,
        )
        ledger.register_work_item(
            work_item_id=wid,
            kind="query",
            engine="duckhaven",
            scale_factor=1,
            scenario="sequential",
            query_id=q,
            rep=1,
        )
        ids.append(wid)
    ledger.mark_running(ids[0])
    ledger.mark_done(ids[0])
    ledger.mark_running(ids[1])
    ledger.mark_failed(ids[1])
    # ids[2] stays pending.

    pending = ledger.pending_work_item_ids(ids)

    assert pending == [ids[1], ids[2]], "done is excluded; failed and pending are still owed work"


def test_pending_work_item_ids_of_empty_list_is_empty(ledger):
    assert ledger.pending_work_item_ids([]) == []


def test_record_query_result_is_upsert_safe(ledger):
    """Re-ingesting the same result (e.g. from a replayed WAL) must not
    duplicate the row — one work item, one result, always."""
    wid = work_item_id(
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    ledger.register_work_item(
        work_item_id=wid,
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    ledger.record_query_result(work_item_id=wid, server_duration_ms=120.0, row_count=42)
    ledger.record_query_result(work_item_id=wid, server_duration_ms=120.0, row_count=42)

    rows = ledger.conn.execute(
        "SELECT count(*) FROM query_results WHERE work_item_id = ?", [wid]
    ).fetchone()[0]
    assert rows == 1


def test_record_query_result_upsert_takes_the_latest_value(ledger):
    """A retried attempt's result should overwrite the prior attempt's,
    not conflict-and-vanish or duplicate."""
    wid = work_item_id(
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    ledger.register_work_item(
        work_item_id=wid,
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    ledger.record_query_result(work_item_id=wid, server_duration_ms=999.0, row_count=1)
    ledger.record_query_result(work_item_id=wid, server_duration_ms=120.0, row_count=42)

    duration, count = ledger.conn.execute(
        "SELECT server_duration_ms, row_count FROM query_results WHERE work_item_id = ?", [wid]
    ).fetchone()
    assert duration == 120.0
    assert count == 42


def test_record_load_result_is_upsert_safe(ledger):
    wid = work_item_id(
        kind="load",
        engine="databricks",
        scale_factor=100,
        scenario=None,
        query_id="lineitem",
        rep=0,
    )
    ledger.register_work_item(
        work_item_id=wid,
        kind="load",
        engine="databricks",
        scale_factor=100,
        query_id="lineitem",
        rep=0,
    )
    ledger.record_load_result(work_item_id=wid, table_name="lineitem", rows_loaded=600_000_000)
    ledger.record_load_result(work_item_id=wid, table_name="lineitem", rows_loaded=600_000_000)

    rows = ledger.conn.execute(
        "SELECT count(*) FROM load_results WHERE work_item_id = ?", [wid]
    ).fetchone()[0]
    assert rows == 1


def test_record_infra_event_is_upsert_safe_on_its_natural_key(ledger):
    """Two provisioning events for the same agent at the same instant is the
    replay case (the same event ingested twice); a *different* started_at
    is a genuinely new event and must not collide."""
    event = dict(
        engine="duckhaven",
        scale_factor=100,
        resource_ref="dh-agent-abc",
        action="provision",
        requested_size="4vcpu/16gb",
        hourly_rate=0.28,
        started_at="2026-08-07T10:00:00+00:00",
    )
    ledger.record_infra_event(**event)
    ledger.record_infra_event(**event)

    rows = ledger.conn.execute("SELECT count(*) FROM infra_events").fetchone()[0]
    assert rows == 1

    ledger.record_infra_event(**{**event, "started_at": "2026-08-07T11:00:00+00:00"})
    rows = ledger.conn.execute("SELECT count(*) FROM infra_events").fetchone()[0]
    assert rows == 2


def test_record_cost_fact_upsert_updates_amount_on_reconciliation(ledger):
    """Cost reconciliation runs hours or days later as billing-latency
    windows catch up (plan §6) — a later pull for the same window must
    replace the earlier (possibly incomplete) figure, not add to it."""
    fact = dict(
        engine="snowflake",
        scale_factor=100,
        scenario="sequential",
        window_start="2026-08-07T10:00:00+00:00",
        cost_amount=1.5,
        currency="USD",
        source="ACCOUNT_USAGE",
        pulled_at="2026-08-07T10:05:00+00:00",
    )
    ledger.record_cost_fact(**fact)
    ledger.record_cost_fact(
        **{**fact, "cost_amount": 4.2, "pulled_at": "2026-08-08T09:00:00+00:00"}
    )

    rows = ledger.conn.execute("SELECT cost_amount FROM cost_facts").fetchall()
    assert rows == [(4.2,)]


def test_record_terraform_session_is_upsert_safe(ledger):
    session = dict(
        session_id="s1", applied_at="2026-08-07T10:00:00+00:00", resource_group="rg-tpch"
    )
    ledger.record_terraform_session(**session)
    ledger.record_terraform_session(**{**session, "destroyed_at": "2026-08-07T12:00:00+00:00"})

    rows = ledger.conn.execute("SELECT count(*) FROM terraform_sessions").fetchone()[0]
    assert rows == 1


def test_methodology_registration_is_idempotent(ledger):
    ledger.register_methodology("abc123", "METHODOLOGY.md")
    ledger.register_methodology("abc123", "METHODOLOGY.md")

    assert ledger.is_methodology_frozen("abc123")
    rows = ledger.conn.execute("SELECT count(*) FROM methodology_registrations").fetchone()[0]
    assert rows == 1


def test_methodology_not_frozen_before_registration(ledger):
    assert not ledger.is_methodology_frozen("never-registered")


def test_upsert_from_wal_replays_work_item_and_result(ledger):
    wid = work_item_id(
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    events = [
        {
            "table": "work_items",
            "row": {
                "work_item_id": wid,
                "kind": "query",
                "engine": "duckhaven",
                "scale_factor": 1,
                "scenario": "sequential",
                "query_id": "q01",
                "rep": 1,
                "status": "done",
                "attempt": 1,
            },
        },
        {"table": "query_results", "row": {"work_item_id": wid, "server_duration_ms": 42.0}},
    ]

    applied = ledger.upsert_from_wal(events)

    assert applied == 2
    assert ledger.status(wid) == "done"
    result_row = ledger.conn.execute(
        "SELECT server_duration_ms FROM query_results WHERE work_item_id = ?", [wid]
    ).fetchone()
    assert result_row == (42.0,)


def test_upsert_from_wal_replayed_twice_is_a_no_op(ledger):
    """The exact scenario the plan calls out: ingesting WALs from three
    separate destroy/recreate sessions must not triple-count anything."""
    wid = work_item_id(
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )
    events = [
        {
            "table": "work_items",
            "row": {
                "work_item_id": wid,
                "kind": "query",
                "engine": "duckhaven",
                "scale_factor": 1,
                "scenario": "sequential",
                "query_id": "q01",
                "rep": 1,
                "status": "done",
                "attempt": 1,
            },
        },
        {"table": "query_results", "row": {"work_item_id": wid, "server_duration_ms": 42.0}},
    ]

    ledger.upsert_from_wal(events)
    ledger.upsert_from_wal(events)
    ledger.upsert_from_wal(events)

    work_item_rows = ledger.conn.execute("SELECT count(*) FROM work_items").fetchone()[0]
    result_rows = ledger.conn.execute("SELECT count(*) FROM query_results").fetchone()[0]
    assert work_item_rows == 1
    assert result_rows == 1


def test_upsert_from_wal_rejects_an_unknown_table(ledger):
    with pytest.raises(ValueError, match="Unknown WAL table"):
        ledger.upsert_from_wal([{"table": "not_a_real_table", "row": {}}])


def test_ledger_reopened_from_disk_keeps_prior_state(tmp_path):
    """A fresh process pointed at the same file must see everything a prior
    process wrote — this is what makes a destroy/recreate cycle's ledger
    durable on the operator's own machine (plan §5), independent of Azure."""
    db_path = tmp_path / "results.duckdb"
    wid = work_item_id(
        kind="query",
        engine="duckhaven",
        scale_factor=1,
        scenario="sequential",
        query_id="q01",
        rep=1,
    )

    with Ledger(db_path) as led:
        led.register_work_item(
            work_item_id=wid,
            kind="query",
            engine="duckhaven",
            scale_factor=1,
            scenario="sequential",
            query_id="q01",
            rep=1,
        )
        led.mark_running(wid)
        led.mark_done(wid)

    with Ledger(db_path) as reopened:
        assert reopened.status(wid) == "done"
