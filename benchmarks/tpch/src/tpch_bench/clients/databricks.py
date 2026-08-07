"""Databricks client — read baseline comparator #2.

Wraps `databricks-sql-connector` directly (the official, published SDK —
see plan §1 and `clients/snowflake.py`'s docstring for why neither
comparator gets a hand-rolled client). Targets a **Serverless SQL
Warehouse only** (plan §4/Risks): the `http_path` passed in must point at
one, since that's the one compute mode whose billing stays inside
Databricks' own DBU metering with no separate cloud VM bill.

Auth is the trial's AWS-hosted workspace OAuth service principal
(client id/secret), confirmed AWS via plan §5. `databricks-sql-connector`
has a built-in `auth_type` only for the *Azure* service-principal M2M flow
(`AzureServicePrincipalCredentialProvider`); a plain Databricks-native
service principal has no equivalent built-in path, so this client mints
its own bearer token with the standard OAuth client-credentials request
against the workspace's `/oidc/v1/token` endpoint and passes it as
`access_token` — the same mechanism the SDK uses internally for Azure,
just aimed at the token endpoint Databricks documents for this auth type.

Known limitation, disclosed rather than silently handled: a minted token
is valid for about an hour, and this client does not refresh one under an
open connection (the SDK's `access_token` is fixed for the connection's
lifetime; a real refresh would require closing and reopening the
session mid-scenario). `sequential` scenarios approaching that lifetime
at large scale factors are a real risk — see plan §9 open risks — but
not one to design around before it's observed happening.

Query-level timing/scan detail is authoritatively available only from the
`system.query.history` Unity Catalog system table, which lags live
activity (typically minutes, not the near-real-time
`INFORMATION_SCHEMA.QUERY_HISTORY_BY_SESSION` Snowflake offers — see
`clients/snowflake.py`). Querying it synchronously inside `run_statement`
would usually return nothing yet and would fold table-scan latency into
`client_wall_ms`, corrupting the very timing this harness measures. So
unlike the DuckHaven and Snowflake clients, `run_statement` here leaves
`server_duration_ms`/`execution_ms`/`bytes_scanned`/etc. as `None`;
backfilling them from `system.query.history` by `engine_query_id` is a
job for a later reconciliation pass over the ledger, not this client.
"""

from __future__ import annotations

import time

import httpx
from databricks import sql as dbsql

from tpch_bench.clients.base import EngineClient, QueryResult

_TOKEN_ENDPOINT = "/oidc/v1/token"


def fetch_oauth_token(*, server_hostname: str, client_id: str, client_secret: str) -> str:
    response = httpx.post(
        f"https://{server_hostname}{_TOKEN_ENDPOINT}",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials", "scope": "all-apis"},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["access_token"]


class DatabricksClient(EngineClient):
    def __init__(
        self,
        *,
        server_hostname: str,
        http_path: str,
        client_id: str,
        client_secret: str,
        catalog: str | None = None,
        schema: str | None = None,
        user_agent_entry: str = "tpch-bench",
    ) -> None:
        self._server_hostname = server_hostname
        self._http_path = http_path
        self._client_id = client_id
        self._client_secret = client_secret
        self._catalog = catalog
        self._schema = schema
        self._user_agent_entry = user_agent_entry
        self._conn: dbsql.Connection | None = None

    # ── EngineClient ─────────────────────────────────────────────────────

    def connect(self) -> None:
        if self._conn is not None:
            return
        token = fetch_oauth_token(
            server_hostname=self._server_hostname,
            client_id=self._client_id,
            client_secret=self._client_secret,
        )
        self._conn = dbsql.connect(
            server_hostname=self._server_hostname,
            http_path=self._http_path,
            access_token=token,
            catalog=self._catalog,
            schema=self._schema,
            user_agent_entry=self._user_agent_entry,
        )

    def run_statement(self, sql: str, *, timeout_s: float) -> QueryResult:
        if self._conn is None:
            self.connect()
        assert self._conn is not None

        wall_start = time.monotonic()
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql)
        except dbsql.Error as exc:
            return QueryResult(
                client_wall_ms=(time.monotonic() - wall_start) * 1000,
                error=str(exc),
            )
        # A SELECT reports no rowcount until its rows are fetched (unlike
        # DuckHaven/Snowflake, whose server responses carry a row count up
        # front); a DML statement's affected-row count is already on
        # cursor.rowcount without a fetch. See module docstring's framing:
        # fetching results is itself part of a real client's roundtrip, so
        # this is the correct cost to include here, not a shortcut to avoid.
        try:
            row_count = len(cursor.fetchall()) if cursor.description is not None else None
        except dbsql.Error as exc:
            cursor.close()
            return QueryResult(
                client_wall_ms=(time.monotonic() - wall_start) * 1000,
                error=str(exc),
            )
        if row_count is None and cursor.rowcount >= 0:
            row_count = cursor.rowcount
        client_wall_ms = (time.monotonic() - wall_start) * 1000
        query_id = cursor.query_id
        cursor.close()

        return QueryResult(
            engine_query_id=query_id,
            client_wall_ms=client_wall_ms,
            row_count=row_count,
            compute_ref=self._http_path,
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
