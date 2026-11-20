"""Index History's duration sort and its text search

Revision ID: 0071
Revises: 0070
Create Date: 2026-11-20

History sorted by duration, and History searched with ``q=``, both read every
run in ``queries``: 240 ms and 360 ms at half a million rows, against 0.5 ms for
the default sort, and growing with the table.

- ``ix_queries_workspace_duration`` is the duration History sorts on, as
  ``api.services.query_history.duration_expr`` compiles it for Postgres, in the
  order the sort reads it: newest-first pages become an index scan. The
  expression is spelled out here rather than imported, so the migration stays
  what it was when it ran; ``tests/integration/test_history_indexes.py`` fails
  if the two stop matching.
- ``ix_queries_sql_trgm`` is a trigram index on the statement text, which a
  case-insensitive ``LIKE '%term%'`` can use for terms of three characters or
  more. It needs the ``pg_trgm`` extension. Where it cannot be created (a
  managed Postgres that does not allow it), the index is skipped with a
  warning and search keeps working as before, unindexed; the upgrade does not
  fail.

Postgres-only, as 0069 is. Building both reads the table once; no rows change.
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

DURATION = (
    "coalesce(duration_ms, "
    "CAST((EXTRACT(EPOCH FROM (finished_at - started_at)) * 1000) AS INTEGER))"
)

TRIGRAM_INDEX = "CREATE INDEX ix_queries_sql_trgm ON queries USING gin (sql gin_trgm_ops)"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(
        "CREATE INDEX ix_queries_workspace_duration ON queries "
        f"(workspace_id, ({DURATION}) DESC NULLS LAST, id DESC)"
    )
    try:
        # A savepoint, so a refusal (the extension not allowed, or installed in a
        # schema outside the search path) leaves the rest of the upgrade standing.
        with bind.begin_nested():
            bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            bind.execute(sa.text(TRIGRAM_INDEX))
    except sa.exc.DBAPIError as exc:
        logger.warning(
            "Could not index History search with pg_trgm (%s); search stays unindexed. "
            "Allow the extension and run `%s` to index it.",
            exc.orig,
            TRIGRAM_INDEX,
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # The extension stays: other objects may have come to depend on it.
    op.execute("DROP INDEX IF EXISTS ix_queries_sql_trgm")
    op.execute("DROP INDEX IF EXISTS ix_queries_workspace_duration")
