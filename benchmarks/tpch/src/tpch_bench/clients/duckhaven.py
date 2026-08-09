"""DuckHaven client — the engine actually under test.

Drives the SQL Sessions API (not the one-shot `queries` endpoint, and not
raw DuckDB): a persistent, connection-scoped session, authenticated with a
service-account PAT, exactly the way a real external client — dbt, a BI
tool, this benchmark — is expected to connect. That's deliberate: the
comparison is supposed to include DuckHaven's own API-roundtrip overhead,
the same overhead Snowflake's and Databricks' own client SDKs carry for
those engines.

Wraps the published `duckhaven-sql-connector` package (PyPI, also
https://github.com/tamasmrtn/duckhaven-clients) rather than reimplementing
its session-open/submit/poll/close machinery: that package is the real,
maintained DB-API 2.0 client, and this harness should exercise it exactly
as any other caller would, not a hand-rolled substitute.

Its public PEP 249 surface deliberately doesn't expose the per-query
timing/memory detail this benchmark needs (duration_ms, the queued-vs-
running-vs-finished split, agent_id, peak_memory_bytes, spill_bytes) — see
`_fetch_metadata`. Two supplementary GETs cover that gap, issued through
the connection's own `Transport` (same auth, retry, and hooks the connector
already set up) rather than a second HTTP client.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from duckhaven_sql_connector import connect as _connect
from duckhaven_sql_connector.connection import Connection
from duckhaven_sql_connector.dbapi import Error as DuckHavenError

from tpch_bench.clients.base import EngineClient, QueryResult


def _ms_between(start_iso: str | None, end_iso: str | None) -> float | None:
    if start_iso is None or end_iso is None:
        return None
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    return (end - start).total_seconds() * 1000


class DuckHavenClient(EngineClient):
    def __init__(
        self,
        *,
        host: str,
        workspace: str,
        pat: str,
        catalog: str | None = None,
        schema: str | None = None,
        agent_id: str | None = None,
        compute_wait_s: float = 300.0,
        application: str = "tpch-bench",
    ) -> None:
        self._host = host
        self._workspace = workspace
        self._pat = pat
        self._catalog = catalog
        self._schema = schema
        self._agent_id = agent_id
        self._compute_wait_s = compute_wait_s
        self._application = application
        self._conn: Connection | None = None

    # ── EngineClient ─────────────────────────────────────────────────────

    def connect(self) -> None:
        if self._conn is not None:
            return
        self._conn = _connect(
            host=self._host,
            workspace=self._workspace,
            token=self._pat,
            agent=self._agent_id,
            catalog=self._catalog,
            schema=self._schema,
            compute_wait=self._compute_wait_s,
            application=self._application,
        )

    def run_statement(self, sql: str, *, timeout_s: float) -> QueryResult:
        if self._conn is None:
            self.connect()
        assert self._conn is not None

        # The connector fixes the per-statement server timeout at Connection
        # construction time (ClientConfig.timeout, sent as the statement
        # body's timeout_s) rather than accepting one per call. Scenarios
        # and scale factors need different timeouts on a session that stays
        # open across many statements (sequential scenario), so this mutates
        # the connector's own config ahead of each execute() rather than
        # reopening a session per statement just to change one number.
        self._conn._config.timeout = timeout_s

        wall_start = time.monotonic()
        try:
            cursor = self._conn.cursor()
            cursor.execute(sql)
        except DuckHavenError as exc:
            return QueryResult(
                client_wall_ms=(time.monotonic() - wall_start) * 1000,
                error=str(exc),
            )
        client_wall_ms = (time.monotonic() - wall_start) * 1000

        # cursor._query_id: not on the public Cursor surface — see module
        # docstring on why the supplementary metadata calls below exist.
        query_id = cursor._query_id
        detail, profile = self._fetch_metadata(query_id)
        row_count = cursor.rowcount if cursor.rowcount >= 0 else None
        # GET /queries/{id}/profile returns {"summary": {...}, "tree": {...}}
        # (agent/src/agent/executor/plan.py: parse_profile / QuerySummary) —
        # peak_memory_bytes/spill_bytes live under "summary", not at the top
        # level. Reading them off the top-level dict silently returned None
        # for every query this harness ever ran until this fix.
        summary = (profile or {}).get("summary") or {}

        return QueryResult(
            engine_query_id=query_id,
            server_duration_ms=(detail or {}).get("duration_ms"),
            queued_ms=_ms_between(
                (detail or {}).get("started_at"), (detail or {}).get("running_at")
            ),
            execution_ms=_ms_between(
                (detail or {}).get("running_at"), (detail or {}).get("finished_at")
            ),
            client_wall_ms=client_wall_ms,
            row_count=row_count,
            peak_memory_bytes=summary.get("peak_memory_bytes"),
            spill_bytes=summary.get("spill_bytes"),
            compute_ref=self._conn.agent_id,
            raw_response_json=detail,
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Supplementary metadata (see module docstring) ───────────────────

    def _fetch_metadata(
        self, query_id: str | None
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if query_id is None or self._conn is None:
            return None, None
        transport = self._conn._transport
        detail = transport.get(f"/queries/{query_id}").json()
        profile = None
        if detail.get("status") == "done":
            try:
                profile = transport.get(f"/queries/{query_id}/profile").json()
            except DuckHavenError as exc:
                if exc.status_code != 404:
                    raise
        return detail, profile
