"""History's indexes against real Postgres (migrations 0070 and 0071).

The unit suite runs on SQLite, which has neither the expression nor the trigram
index. These tests apply the two migrations to the session's schema and ask the
planner, with sequential scans priced out, whether History's statements and the
agent foreign-key lookups can use them. A plan that still reads ``queries`` from
start to end means an index no longer matches the statement it was built for.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql

from api.models.query import Query
from api.services import query_history

pytestmark = pytest.mark.integration

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _module(filename: str):
    spec = importlib.util.spec_from_file_location(filename, VERSIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade(sync_conn, module) -> None:
    with Operations.context(MigrationContext.configure(sync_conn)):
        module.upgrade()


@pytest.fixture
async def indexed(db_session):
    """The session's schema with 0070 and 0071 applied over ``create_all``."""
    conn = await db_session.connection()
    await conn.execute(text("CREATE INDEX ix_credentials_token ON credentials (token)"))
    await conn.run_sync(_upgrade, _module("0070_agent_foreign_key_indexes.py"))
    await conn.run_sync(_upgrade, _module("0071_queries_history_indexes.py"))
    await conn.execute(text("SET LOCAL enable_seqscan = off"))
    return conn


async def _plan(conn, stmt) -> str:
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    rows = (await conn.execute(text(f"EXPLAIN {sql}"))).scalars().all()
    return "\n".join(rows)


async def _index_exists(conn, name: str) -> bool:
    return (
        await conn.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name})
    ).scalar_one()


async def test_the_duration_sort_reads_the_duration_index(indexed):
    stmt = (
        select(Query.id, query_history.duration_expr())
        .where(Query.workspace_id == uuid.uuid4())
        .order_by(*query_history.order_by("duration", "desc"))
        .limit(51)
    )
    plan = await _plan(indexed, stmt)
    assert "ix_queries_workspace_duration" in plan, plan
    assert "Sort" not in plan, plan


async def test_text_search_can_read_the_trigram_index(indexed):
    """The search predicate alone, so the index has to match it: with the workspace
    filter too, an empty table's planner prefers a workspace index whatever the
    search. Which the planner picks on real data is measured, not asserted here."""
    if not await _index_exists(indexed, "ix_queries_sql_trgm"):
        pytest.skip("pg_trgm is not available on this Postgres; 0071 skipped the index")
    stmt = select(Query.id).where(query_history.search_predicate("group by"))
    plan = await _plan(indexed, stmt)
    assert "ix_queries_sql_trgm" in plan, plan


@pytest.mark.parametrize(
    ("table", "column"), _module("0070_agent_foreign_key_indexes.py").FOREIGN_KEYS
)
async def test_finding_an_agents_rows_reads_an_index(indexed, table, column):
    """What deleting an agent asks of each referencing table, once per agent."""
    plan = "\n".join(
        (
            await indexed.execute(
                text(f"EXPLAIN SELECT 1 FROM {table} WHERE {column} = :id"), {"id": uuid.uuid4()}
            )
        )
        .scalars()
        .all()
    )
    assert "Seq Scan" not in plan, plan
