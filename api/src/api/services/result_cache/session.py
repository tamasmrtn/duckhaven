"""The result cache inside SQL sessions.

A one-shot query runs on a fresh connection, so its result depends on nothing
but the query, its catalog context and the data. A session statement runs on a
connection that remembers what came before, and three things can make the same
statement mean something else there:

- **An open transaction.** DuckDB reads a table once per transaction and keeps
  that version, so a read late in a transaction can see an older version than the
  catalog reports (pinned by the agent's integration suite), and it sees the
  transaction's own uncommitted writes. Statements inside a transaction never
  use the cache — the same rule TxCache applies to read/write transactions.
- **Session-local objects.** A temporary table, view or macro shadows a catalog
  object of the same name, a `search_path` widens where names resolve, and an
  extra `ATTACH` adds a catalog. A session that has done any of these is
  *tainted* for its remaining life.
- **The binding context.** `USE` and `SET TimeZone` move the catalog, schema and
  time zone a statement resolves in. Those do not taint: the agent reports the
  context the connection is in after every statement, and it becomes part of the
  key exactly as for a one-shot query.

The state lives on the `sql_sessions` row (Postgres is the state of record, so
any replica can serve the next statement) and is updated in the same transaction
that records the statement's completion, so a client that sends its next
statement the moment it hears of this one never sees a stale state.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
import sqlglot
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp

from api.config import settings
from api.models.agent import Agent
from api.models.catalog import Catalog
from api.models.query import Query
from api.models.sql_session import SqlSession
from api.models.workspace import Workspace
from api.services.result_cache import service
from api.services.result_cache.key import CacheContext

# SET targets whose effect the agent's reported context already captures.
_CONTEXT_SETTINGS = frozenset({"timezone", "time zone", "schema"})
_READS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)


def initial_state(session: SqlSession, agent: Agent | None, default_catalog: str) -> dict:
    """What a freshly opened session connection resolves in: the agent `USE`s the
    session's catalog and the default schema, in the agent's own time zone."""
    from api.services.workspace import DEFAULT_SCHEMA

    timezone = (agent.capabilities or {}).get("timezone") if agent is not None else None
    return {
        "catalog": session.active_catalog or default_catalog,
        "schema": DEFAULT_SCHEMA,
        "timezone": timezone,
        "in_txn": False,
        "tainted": False,
        "tainted_reason": None,
    }


def _taint(state: dict, reason: str) -> None:
    if not state.get("tainted"):
        state["tainted"] = True
        state["tainted_reason"] = reason


def _setting_name(item: exp.Expression) -> str:
    column = item.find(exp.Column)
    return column.name.lower() if column is not None else ""


def reduce(state: dict, sql: str, *, succeeded: bool, reported: dict | None) -> dict:
    """The session's state after ``sql`` ran (or failed)."""
    new = dict(state)
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except Exception:  # noqa: BLE001 - unparseable: assume the worst
        _taint(new, "unparsed")
        return new
    for stmt in statements:
        if isinstance(stmt, exp.Transaction):
            if succeeded:
                new["in_txn"] = True
        elif isinstance(stmt, exp.Commit | exp.Rollback):
            # A failed COMMIT rolls back in DuckDB: either way the transaction ends.
            new["in_txn"] = False
        elif isinstance(stmt, exp.Create) and (
            stmt.find(exp.TemporaryProperty) is not None
            or str(stmt.kind or "").upper() in ("MACRO", "FUNCTION")
        ):
            _taint(new, "temporary_object")
        elif isinstance(stmt, exp.Set):
            if any(_setting_name(i) not in _CONTEXT_SETTINGS for i in stmt.expressions):
                _taint(new, "setting")
        elif isinstance(stmt, exp.Attach):
            _taint(new, "attach")
        elif isinstance(stmt, exp.Command):
            _taint(new, "unparsed")
        if not succeeded and not isinstance(stmt, _READS):
            # How far a failed script got is unknown: a BEGIN or a USE before the
            # failing statement may or may not have taken effect.
            _taint(new, "failed_statement")
    if reported:
        new["catalog"] = reported.get("catalog", new.get("catalog"))
        new["schema"] = reported.get("schema", new.get("schema"))
        new["timezone"] = reported.get("timezone", new.get("timezone"))
    return new


async def record_completion(db: AsyncSession, query: Query, payload: dict[str, Any]) -> None:
    """Fold a finished statement into its session's cache state. Called by the
    QUERY_DONE handler before it commits the statement's terminal status."""
    if query.origin != "session" or query.session_id is None:
        return
    session = await db.get(SqlSession, query.session_id)
    if session is None:
        return
    state = session.cache_state
    if state is None:
        agent = await db.get(Agent, session.agent_id) if session.agent_id is not None else None
        state = initial_state(session, agent, session.active_catalog or "")
    session.cache_state = reduce(
        state,
        query.sql,
        succeeded=payload.get("status", "done") == "done",
        reported=payload.get("session_context"),
    )


async def _busy(db: AsyncSession, session_id: uuid.UUID) -> bool:
    """Whether another statement of the session is still queued or running: its
    effect on the session's state is not known yet."""
    count = await db.scalar(
        sa.select(sa.func.count())
        .select_from(Query)
        .where(Query.session_id == session_id, Query.status.in_(("queued", "running")))
    )
    return bool(count)


async def lookup(
    db: AsyncSession,
    *,
    session: SqlSession,
    workspace: Workspace,
    catalogs: list[Catalog],
    sql: str,
    use_cache: bool,
    polaris: Any,
) -> service.Lookup:
    """Whether a session statement can be answered from the cache right now."""
    if not session.use_cache:
        return service.Lookup(status=service.BYPASS, detail="opted_out")
    if await _busy(db, session.id):
        return service.Lookup(status=service.BYPASS, detail="session_busy")
    state = session.cache_state
    if state is None:
        agent = await db.get(Agent, session.agent_id) if session.agent_id is not None else None
        default = catalogs[0].slug if catalogs else ""
        state = initial_state(session, agent, default)
    if state.get("in_txn"):
        return service.Lookup(status=service.INELIGIBLE, detail="in_transaction")
    if state.get("tainted"):
        return service.Lookup(status=service.INELIGIBLE, detail="session_state")
    if not state.get("timezone") or not state.get("catalog"):
        return service.Lookup(status=service.BYPASS, detail="unknown_context")
    context = CacheContext(
        current_catalog=state["catalog"],
        current_schema=state["schema"],
        runtime_id=session.runtime_id or settings.default_runtime,
        timezone=state["timezone"],
    )
    return await service.lookup(
        db,
        workspace=workspace,
        catalogs=catalogs,
        sql=sql,
        origin="session",
        use_cache=use_cache,
        context=context,
        polaris=polaris,
    )


def context_matches(session: SqlSession | None, recorded: dict) -> bool:
    """For admission: the session is still in the context the statement was keyed
    in, with no transaction open and nothing session-local created. A cacheable
    statement is a plain read and cannot change any of that, so a difference means
    something else happened and the result is not kept."""
    if session is None or session.cache_state is None:
        return False
    state = session.cache_state
    return (
        not state.get("in_txn")
        and not state.get("tainted")
        and state.get("catalog") == recorded.get("current_catalog")
        and state.get("schema") == recorded.get("current_schema")
        and state.get("timezone") == recorded.get("timezone")
        and (session.runtime_id or settings.default_runtime) == recorded.get("runtime_id")
    )
