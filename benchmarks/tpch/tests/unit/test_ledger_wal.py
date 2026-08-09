"""The WAL is the durability layer under the ledger: everything here is
really testing "does this survive the process dying between the WAL write
and the DuckDB write?"
"""

from __future__ import annotations

from tpch_bench.ledger.wal import WalWriter, read_events


def test_write_then_read_round_trips(tmp_path):
    wal_path = tmp_path / "run" / "wal.jsonl"
    with WalWriter(wal_path) as wal:
        wal.write("work_items", {"work_item_id": "abc", "status": "pending"})
        wal.write("query_results", {"work_item_id": "abc", "server_duration_ms": 12.5})

    events = read_events(wal_path)

    assert events == [
        {"table": "work_items", "row": {"work_item_id": "abc", "status": "pending"}},
        {"table": "query_results", "row": {"work_item_id": "abc", "server_duration_ms": 12.5}},
    ]


def test_read_events_of_a_missing_file_is_empty_not_an_error(tmp_path):
    """A run that crashed before its very first write must still be
    "replayable" — as a no-op, not a crash in the recovery path itself."""
    assert read_events(tmp_path / "never-written.jsonl") == []


def test_events_are_returned_in_append_order(tmp_path):
    """Replay correctness depends on this: `Ledger.upsert_from_wal` converges
    to the *last* write for a given key, which is only correct if events
    come back in the order they actually happened."""
    wal_path = tmp_path / "wal.jsonl"
    with WalWriter(wal_path) as wal:
        for i in range(5):
            wal.write("work_items", {"work_item_id": "abc", "attempt": i})

    events = read_events(wal_path)

    assert [e["row"]["attempt"] for e in events] == [0, 1, 2, 3, 4]


def test_writer_appends_across_separate_instances(tmp_path):
    """Simulates a resumed run: a fresh process, a fresh WalWriter, same
    path — nothing from the earlier session's writes may be lost."""
    wal_path = tmp_path / "wal.jsonl"
    with WalWriter(wal_path) as wal:
        wal.write("work_items", {"work_item_id": "first-session"})

    with WalWriter(wal_path) as wal:
        wal.write("work_items", {"work_item_id": "second-session"})

    events = read_events(wal_path)
    assert [e["row"]["work_item_id"] for e in events] == ["first-session", "second-session"]


def test_non_json_native_values_are_stringified(tmp_path):
    """Timestamps and similar objects (e.g. datetimes from a live API
    response) must not crash the writer — the WAL is a durability log, not
    a strict schema, so best-effort string coercion beats losing the event."""
    import datetime

    wal_path = tmp_path / "wal.jsonl"
    with WalWriter(wal_path) as wal:
        wal.write("infra_events", {"started_at": datetime.datetime(2026, 8, 7, 10, 0)})

    events = read_events(wal_path)
    assert events[0]["row"]["started_at"] == "2026-08-07 10:00:00"
