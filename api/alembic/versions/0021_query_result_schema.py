"""Query result_schema

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-20

Adds ``queries.result_schema`` — the result's column types as the executing agent
reported them, ``[{"name", "type"}, ...]`` with ``type`` spelled the way DuckDB
itself prints a logical type.

Results were previously returned as untyped JSON: the column names survived to
``RowsPageOut.columns`` but the types did not, so clients had to guess (dlt's
timestamp-string sniffing). The types cannot be recovered from the materialized
Parquet — its writer is lossy (``HUGEINT`` -> ``DOUBLE``, ``ENUM`` -> ``VARCHAR``,
``INTEGER[2]`` -> ``INTEGER[]``, ``BIT`` -> ``VARCHAR``) — so the agent captures
them off the DuckDB relation before materialization and reports them on
``QUERY_DONE``.

Nullable: DDL/DML runs have no result grid, and runs by an agent older than this
release report nothing. Both leave the column null, and the API reports no schema
rather than deriving a wrong one. Additive — no data migration.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "queries",
        sa.Column("result_schema", sa.JSON().with_variant(postgresql.JSONB, "postgresql")),
    )


def downgrade() -> None:
    op.drop_column("queries", "result_schema")
