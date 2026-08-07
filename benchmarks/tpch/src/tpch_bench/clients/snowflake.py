"""Snowflake client — read baseline comparator #1.

Wraps `snowflake-connector-python` directly (the official, published SDK —
see plan §1; there is no case for hand-rolling a client against either
comparator engine when its own vendor SDK already carries the same kind of
roundtrip overhead this benchmark wants to capture). One
`SnowflakeConnection` per `connect()`/`close()` cycle, matching
`DuckHavenClient`'s session-scoped design so the `cold_start` scenario's
per-query reconnect cycle behaves the same way across engines.

Query-level timing/scan detail (TOTAL_ELAPSED_TIME, BYTES_SCANNED, spill,
...) comes from `INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION`, a
near-real-time table function scoped to the calling session. That's a
deliberate choice over `ACCOUNT_USAGE.QUERY_HISTORY`, whose views lag live
activity by up to a few hours (see plan §6 step 11, which already budgets
for that lag on the *cost* side) — the equivalent of DuckHaven's
`GET /queries/{id}/profile` supplementary call in `clients/duckhaven.py`,
fetched only after `cursor.execute()` finishes so it never inflates the
timed call itself.
"""

from __future__ import annotations

import time
from typing import Any

import snowflake.connector
from snowflake.connector import DictCursor, SnowflakeConnection
from snowflake.connector.errors import Error as SnowflakeError

from tpch_bench.clients.base import EngineClient, QueryResult

_HISTORY_QUERY = """
    SELECT total_elapsed_time, execution_time, compilation_time,
           queued_provisioning_time, queued_repair_time, queued_overload_time,
           bytes_scanned, bytes_spilled_to_local_storage,
           bytes_spilled_to_remote_storage, rows_produced
    FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION(RESULT_LIMIT => 100))
    WHERE query_id = %s
"""


class SnowflakeClient(EngineClient):
    def __init__(
        self,
        *,
        account: str,
        user: str,
        password: str,
        warehouse: str,
        database: str,
        schema: str = "PUBLIC",
        role: str | None = None,
        application: str = "tpch-bench",
    ) -> None:
        self._account = account
        self._user = user
        self._password = password
        self._warehouse = warehouse
        self._database = database
        self._schema = schema
        self._role = role
        self._application = application
        self._conn: SnowflakeConnection | None = None

    # ── EngineClient ─────────────────────────────────────────────────────

    def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = snowflake.connector.connect(
            account=self._account,
            user=self._user,
            password=self._password,
            warehouse=self._warehouse,
            database=self._database,
            schema=self._schema,
            role=self._role,
            application=self._application,
        )

    def run_statement(self, sql: str, *, timeout_s: float) -> QueryResult:
        if self._conn is None:
            self.connect()
        assert self._conn is not None

        wall_start = time.monotonic()
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, timeout=int(timeout_s))
        except SnowflakeError as exc:
            return QueryResult(
                client_wall_ms=(time.monotonic() - wall_start) * 1000,
                error=str(exc),
            )
        finally:
            cursor.close()
        client_wall_ms = (time.monotonic() - wall_start) * 1000

        query_id = cursor.sfqid
        detail = self._fetch_metadata(query_id)

        return QueryResult(
            engine_query_id=query_id,
            server_duration_ms=(detail or {}).get("total_elapsed_time"),
            queued_ms=_queued_ms(detail),
            execution_ms=(detail or {}).get("execution_time"),
            client_wall_ms=client_wall_ms,
            row_count=cursor.rowcount,
            bytes_scanned=(detail or {}).get("bytes_scanned"),
            spill_bytes=_spill_bytes(detail),
            compute_ref=self._warehouse,
            raw_response_json=detail,
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Supplementary metadata (see module docstring) ───────────────────

    def _fetch_metadata(self, query_id: str | None) -> dict[str, Any] | None:
        if query_id is None or self._conn is None:
            return None
        cursor = self._conn.cursor(DictCursor)
        try:
            cursor.execute(_HISTORY_QUERY, (query_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        except SnowflakeError:
            # History is asked for on a best-effort basis: a lookup failure
            # here must not turn a successful query into a failed result.
            return None
        finally:
            cursor.close()


def _queued_ms(detail: dict[str, Any] | None) -> float | None:
    if not detail:
        return None
    parts = (
        detail.get("queued_provisioning_time"),
        detail.get("queued_repair_time"),
        detail.get("queued_overload_time"),
    )
    if all(p is None for p in parts):
        return None
    return float(sum(p or 0 for p in parts))


def _spill_bytes(detail: dict[str, Any] | None) -> int | None:
    if not detail:
        return None
    local = detail.get("bytes_spilled_to_local_storage")
    remote = detail.get("bytes_spilled_to_remote_storage")
    if local is None and remote is None:
        return None
    return (local or 0) + (remote or 0)
