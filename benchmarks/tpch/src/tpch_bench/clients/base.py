"""The common interface every engine client implements, so the orchestrator
drives DuckHaven/Snowflake/Databricks identically — the "same shape of
client for every engine" half of staying impartial.

Synchronous throughout, matching every real client this harness wraps:
`duckhaven-sql-connector`, `snowflake-connector-python`, and
`databricks-sql-connector` are all blocking DB-API 2.0 style clients, none
async-native. The `concurrent` scenario gets its parallelism from a
`ThreadPoolExecutor` (one connection per worker thread — see
clients/duckhaven.py's note on session serialization), not from asyncio.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class QueryResult:
    """One statement's outcome, in exactly the shape
    `ledger.store.Ledger.record_query_result` expects as keyword arguments."""

    engine_query_id: str | None = None
    server_duration_ms: float | None = None
    queued_ms: float | None = None
    execution_ms: float | None = None
    client_wall_ms: float | None = None
    row_count: int | None = None
    bytes_scanned: int | None = None
    peak_memory_bytes: int | None = None
    spill_bytes: int | None = None
    compute_ref: str | None = None
    error: str | None = None
    raw_response_json: dict[str, Any] | None = None


class EngineClient(ABC):
    """One open session/connection against one engine, for the duration of a
    scenario run. Implementations own their connection's lifecycle; the
    orchestrator only ever sees this interface."""

    @abstractmethod
    def connect(self) -> None:
        """Open whatever the engine calls a session/connection/warehouse
        resume. Idempotent: calling it again while already connected is a
        no-op, which is what makes the `cold_start` scenario's per-query
        disconnect/reconnect cycle simple to drive."""

    @abstractmethod
    def run_statement(self, sql: str, *, timeout_s: float) -> QueryResult:
        """Run one statement to completion (or timeout/error) and return its
        outcome. Never raises for a query-level failure — that's `error` on
        the result, which the ledger records as a normal outcome, not an
        orchestrator-level exception. Only a connection/auth-level problem
        should raise."""

    @abstractmethod
    def close(self) -> None:
        """Close the session/connection. Safe to call when not connected."""

    def __enter__(self) -> EngineClient:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
