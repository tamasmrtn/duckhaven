"""Delegated identity

Revision ID: 0058
Revises: 0057
Create Date: 2026-11-13

The assistant acts for the person who asked, not only as its service account.

- ``credentials.on_behalf_of_id`` names that person on the assistant's per-turn
  token. A request with it may do only what both principals may do. Deleting the
  person deletes the token.
- ``queries.on_behalf_of_id`` records the person on each run the token makes, so
  History can show it to them and a run parked while compute starts is checked
  for both again when it is dispatched. Deleting the person clears it and keeps
  the run. Indexed, because History filters on it.

Both are nullable and additive: existing tokens and runs act for no one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("credentials") as batch:
        batch.add_column(sa.Column("on_behalf_of_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_credentials_on_behalf_of",
            "users",
            ["on_behalf_of_id"],
            ["id"],
            ondelete="CASCADE",
        )
    with op.batch_alter_table("queries") as batch:
        batch.add_column(sa.Column("on_behalf_of_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_queries_on_behalf_of",
            "users",
            ["on_behalf_of_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_queries_on_behalf_of_id", ["on_behalf_of_id"])


def downgrade() -> None:
    with op.batch_alter_table("queries") as batch:
        batch.drop_index("ix_queries_on_behalf_of_id")
        batch.drop_constraint("fk_queries_on_behalf_of", type_="foreignkey")
        batch.drop_column("on_behalf_of_id")
    with op.batch_alter_table("credentials") as batch:
        batch.drop_constraint("fk_credentials_on_behalf_of", type_="foreignkey")
        batch.drop_column("on_behalf_of_id")
