"""Weigh the documentation's table rows below its prose in search

Revision ID: 0069
Revises: 0068
Create Date: 2026-11-20

Postgres full-text ranking has no IDF, so the words a page repeats count for as
much as the words it is about. Reference pages are mostly Markdown tables: the
system catalog's lists ``catalog_name``, ``schema_name``, ``created_at`` and
``catalog_kind`` dozens of times, and the parser splits each into common words.
That pulled it into the top five for questions such as "What kinds of catalog
can I create?", ahead of the page that answers them.

The body is now indexed in two parts: its prose at 'C', as before, and its table
rows at 'D', the lowest weight. A table row still matches, so a column or setting
named only in a table is still found; it just no longer outweighs prose.

Only the generated column changes. Postgres recomputes it for every row when it
is added, so no corpus resync is needed. Postgres-only, as 0041 is.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A table row is a line starting with "|". Each half blanks the other's lines;
# the 'n' flag makes ^ and $ match at line ends and keeps . within a line.
_TABLE_ROWS_AT_D = """
    ALTER TABLE docs_pages ADD COLUMN search tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(summary, '')), 'B') ||
        setweight(to_tsvector('english', regexp_replace(body, '^[|].*$', '', 'gn')), 'C') ||
        setweight(to_tsvector('english', regexp_replace(body, '^[^|].*$', '', 'gn')), 'D')
    ) STORED
"""

# 0041's column, for the downgrade.
_WHOLE_BODY_AT_C = """
    ALTER TABLE docs_pages ADD COLUMN search tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(summary, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(body, '')), 'C')
    ) STORED
"""


def _replace_search_column(definition: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP INDEX IF EXISTS ix_docs_pages_search")
    op.execute("ALTER TABLE docs_pages DROP COLUMN IF EXISTS search")
    op.execute(definition)
    op.execute("CREATE INDEX ix_docs_pages_search ON docs_pages USING gin(search)")


def upgrade() -> None:
    _replace_search_column(_TABLE_ROWS_AT_D)


def downgrade() -> None:
    _replace_search_column(_WHOLE_BODY_AT_C)
