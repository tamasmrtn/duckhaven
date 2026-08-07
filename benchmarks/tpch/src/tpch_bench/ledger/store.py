"""The idempotency ledger and results store: one DuckDB file serving as both
the working ledger and the published raw-results artifact.

Resumption is a pure anti-join: for a requested (engine, sf, scenario),
compute the full expected work-item set, subtract rows already
status='done', execute (and bill) only what's left.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


# Columns that form each upsert table's natural key — excluded from the
# `DO UPDATE SET` clause since a key column can't reassign itself.
_COST_FACT_KEY = {"engine", "scale_factor", "scenario", "window_start", "source"}
_INFRA_EVENT_KEY = {"resource_ref", "action", "started_at"}


def work_item_id(
    *,
    kind: str,
    engine: str,
    scale_factor: int,
    scenario: str | None,
    query_id: str | None,
    rep: int,
) -> str:
    """Deterministic id: the same logical unit of work always hashes to the
    same id, so re-registering it (e.g. on a resumed run) is a no-op rather
    than a duplicate row."""
    raw = f"{kind}|{engine}|{scale_factor}|{scenario}|{query_id}|{rep}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(self.path)
        self.conn.execute(SCHEMA_PATH.read_text())

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Ledger:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── Work items ──────────────────────────────────────────────────────

    def register_work_item(
        self,
        *,
        work_item_id: str,
        kind: str,
        engine: str,
        scale_factor: int,
        scenario: str | None = None,
        query_id: str | None = None,
        rep: int = 0,
        run_id: str | None = None,
        methodology_hash: str | None = None,
    ) -> None:
        """Ensure a work item exists, as `pending`, if it doesn't already.

        `ON CONFLICT DO NOTHING`: this is the "resumed run recomputes its
        full expected work-item set and re-registers all of it" call — it
        must never touch status/attempt on a row that already exists, or
        every resumption would silently reset finished work back to
        pending. Status changes only ever happen through mark_running/
        mark_done/mark_failed (or their WAL-replay equivalent, §below).
        """
        _upsert(
            self.conn,
            "work_items",
            {
                "work_item_id": work_item_id,
                "kind": kind,
                "engine": engine,
                "scale_factor": scale_factor,
                "scenario": scenario,
                "query_id": query_id,
                "rep": rep,
                "status": "pending",
                "attempt": 0,
                "started_at": None,
                "finished_at": None,
                "run_id": run_id,
                "methodology_hash": methodology_hash,
            },
            {"work_item_id"},
            update_cols=set(),  # DO NOTHING on conflict
        )

    def status(self, work_item_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM work_items WHERE work_item_id = ?", [work_item_id]
        ).fetchone()
        return row[0] if row else None

    def is_done(self, work_item_id: str) -> bool:
        return self.status(work_item_id) == "done"

    def mark_running(self, work_item_id: str) -> dict[str, Any]:
        row = self._identity(work_item_id)
        attempt = row.pop("attempt")
        row.pop("started_at")
        row.pop("finished_at")
        return self._set_state(
            work_item_id,
            **row,
            status="running",
            attempt=attempt + 1,
            started_at=_now_iso(),
            finished_at=None,
        )

    def mark_done(self, work_item_id: str) -> dict[str, Any]:
        row = self._identity(work_item_id)
        row.pop("finished_at")
        return self._set_state(work_item_id, **row, status="done", finished_at=_now_iso())

    def mark_failed(self, work_item_id: str) -> dict[str, Any]:
        row = self._identity(work_item_id)
        row.pop("finished_at")
        return self._set_state(work_item_id, **row, status="failed", finished_at=_now_iso())

    def _identity(self, work_item_id: str) -> dict[str, Any]:
        """The row's current identity + state, as kwargs for `_set_state` —
        every transition starts from "what's there now" and changes only
        what it means to change."""
        row = self.conn.execute(
            "SELECT kind, engine, scale_factor, scenario, query_id, rep, attempt, "
            "started_at, finished_at, run_id, methodology_hash "
            "FROM work_items WHERE work_item_id = ?",
            [work_item_id],
        ).fetchone()
        if row is None:
            raise KeyError(f"work item {work_item_id!r} was never registered")
        cols = [
            "kind",
            "engine",
            "scale_factor",
            "scenario",
            "query_id",
            "rep",
            "attempt",
            "started_at",
            "finished_at",
            "run_id",
            "methodology_hash",
        ]
        return dict(zip(cols, row, strict=True))

    def _set_state(
        self,
        work_item_id: str,
        *,
        kind: str,
        engine: str,
        scale_factor: int,
        scenario: str | None = None,
        query_id: str | None = None,
        rep: int = 0,
        status: str,
        attempt: int = 0,
        started_at: str | None = None,
        finished_at: str | None = None,
        run_id: str | None = None,
        methodology_hash: str | None = None,
    ) -> dict[str, Any]:
        """The upsert target for both a live status transition and a
        replayed `work_items` WAL event: full last-write-wins semantics on
        status/attempt/timestamps, which is correct as long as events are
        applied in the order they actually happened (guaranteed for a live
        transition sequence, and guaranteed for WAL replay by
        `wal.read_events` returning append order).

        Returns the full row it wrote, so a caller driving both the WAL and
        the ledger (orchestrator/runner.py) can log the exact same fields
        to the WAL without recomputing attempt/timestamp bookkeeping that
        already lives here.
        """
        fields = {
            "work_item_id": work_item_id,
            "kind": kind,
            "engine": engine,
            "scale_factor": scale_factor,
            "scenario": scenario,
            "query_id": query_id,
            "rep": rep,
            "status": status,
            "attempt": attempt,
            "started_at": started_at,
            "finished_at": finished_at,
            "run_id": run_id,
            "methodology_hash": methodology_hash,
        }
        _upsert(
            self.conn,
            "work_items",
            fields,
            {"work_item_id"},
            update_cols={"status", "attempt", "started_at", "finished_at"},
        )
        return fields

    def pending_work_item_ids(self, ids: list[str]) -> list[str]:
        """Given the full expected work-item id set for a requested
        (engine, sf, scenario), return the subset that is not yet `done` —
        the anti-join resumption relies on. Preserves the input order."""
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        done = {
            row[0]
            for row in self.conn.execute(
                f"SELECT work_item_id FROM work_items "
                f"WHERE work_item_id IN ({placeholders}) AND status = 'done'",
                ids,
            ).fetchall()
        }
        return [i for i in ids if i not in done]

    # ── Results (each 1:1 with the work item that produced it) ─────────

    def record_query_result(self, *, work_item_id: str, **fields: Any) -> None:
        _upsert(
            self.conn, "query_results", {"work_item_id": work_item_id, **fields}, {"work_item_id"}
        )

    def record_load_result(self, *, work_item_id: str, **fields: Any) -> None:
        _upsert(
            self.conn, "load_results", {"work_item_id": work_item_id, **fields}, {"work_item_id"}
        )

    # ── Append-only / upsert-safe logs ──────────────────────────────────

    def record_infra_event(self, **fields: Any) -> None:
        _upsert(self.conn, "infra_events", fields, _INFRA_EVENT_KEY)

    def record_cost_fact(self, **fields: Any) -> None:
        """Upsert-safe: cost reconciliation is designed to be re-run hours
        or days later without duplicating rows for the same billing window."""
        _upsert(self.conn, "cost_facts", fields, _COST_FACT_KEY)

    def record_terraform_session(self, **fields: Any) -> None:
        _upsert(self.conn, "terraform_sessions", fields, {"session_id"})

    def register_methodology(self, methodology_hash: str, doc_path: str) -> None:
        self.conn.execute(
            """INSERT INTO methodology_registrations (methodology_hash, registered_at, doc_path)
               VALUES (?, now(), ?) ON CONFLICT (methodology_hash) DO NOTHING""",
            [methodology_hash, doc_path],
        )

    def is_methodology_frozen(self, methodology_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM methodology_registrations WHERE methodology_hash = ?",
            [methodology_hash],
        ).fetchone()
        return row is not None

    # ── WAL replay ──────────────────────────────────────────────────────

    def upsert_from_wal(self, events: list[dict[str, Any]]) -> int:
        """Replay WAL events idempotently.

        Re-ingesting the same WAL twice, or ingesting WALs from three
        separate destroy/recreate sessions, is a no-op past the first
        ingest for every table above — each has a natural key.

        `work_items` events go through `_set_state`, not `register_work_item`:
        a WAL-logged work-item row is always a state snapshot (it carries
        the status a live transition set), and replay must apply it with
        last-write-wins semantics — the same reasoning `_set_state`'s
        docstring covers for the live path.
        """
        dispatch = {
            "work_items": self._set_state,
            "query_results": self.record_query_result,
            "load_results": self.record_load_result,
            "infra_events": self.record_infra_event,
            "cost_facts": self.record_cost_fact,
            "terraform_sessions": self.record_terraform_session,
        }
        applied = 0
        for event in events:
            table = event["table"]
            handler = dispatch.get(table)
            if handler is None:
                raise ValueError(f"Unknown WAL table {table!r}")
            handler(**event["row"])
            applied += 1
        return applied


def _upsert(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    fields: dict[str, Any],
    key_cols: set[str],
    update_cols: set[str] | None = None,
) -> None:
    """INSERT ... ON CONFLICT (key_cols) DO UPDATE for a natural-keyed table.

    `update_cols` defaults to every non-key column (full-row-replace, right
    for a table where a later write is simply the latest known value of the
    whole row — query_results, cost_facts, ...). Pass it explicitly to
    update only *some* columns on conflict — work_items needs this: its
    "ensure this work item exists" call and its "transition its status"
    call target the same row, but only the second should ever move status
    backwards from what a resumed run's re-registration would otherwise
    silently reset to 'pending'. Falls back to ON CONFLICT DO NOTHING when
    there is nothing left to update.
    """
    cols = list(fields)
    placeholders = ", ".join("?" for _ in cols)
    values = [json.dumps(v) if isinstance(v, dict) else v for v in fields.values()]
    conflict_cols = ", ".join(sorted(key_cols))
    targets = (
        sorted(update_cols) if update_cols is not None else [c for c in cols if c not in key_cols]
    )
    if targets:
        action = "DO UPDATE SET " + ", ".join(f"{c} = excluded.{c}" for c in targets)
    else:
        action = "DO NOTHING"
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_cols}) {action}",
        values,
    )
