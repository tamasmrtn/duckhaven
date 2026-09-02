"""Add docs_pages and docs_corpus_meta, with a Postgres full-text index

Revision ID: 0041
Revises: 0040
Create Date: 2026-09-02

Storage for the documentation corpus the assistant searches. The rows are a
cache of files the image already ships; ``docs/`` stays the source of truth and
the tables are rebuilt from it whenever the content hash changes.

The tsvector column is generated and weighted: a title match ('A') outranks a
summary match ('B'), which outranks a body mention ('C'). Without the weights a
page that merely mentions "storage backends" in passing competes with the page
named that.

Postgres-only, and deliberately so — the table is created everywhere, but the
tsvector and its GIN index are skipped on other dialects so the SQLite unit
suite can still create the schema. Search itself is exercised in the integration
suite, which has a real Postgres.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEARCH_COLUMN = """
    ALTER TABLE docs_pages ADD COLUMN search tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(summary, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(body, '')), 'C')
    ) STORED
"""


def upgrade() -> None:
    op.create_table(
        "docs_pages",
        sa.Column("path", sa.String(255), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("section", sa.String(100), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_table(
        "docs_corpus_meta",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("app_version", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "loaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(_SEARCH_COLUMN)
        op.execute("CREATE INDEX ix_docs_pages_search ON docs_pages USING gin(search)")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_docs_pages_search")
    op.drop_table("docs_corpus_meta")
    op.drop_table("docs_pages")
