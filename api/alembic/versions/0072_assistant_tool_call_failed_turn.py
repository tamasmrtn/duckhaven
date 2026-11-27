"""Mark the tool calls of a turn that failed

Revision ID: 0072
Revises: 0071
Create Date: 2026-11-26

``assistant_tool_calls.from_failed_turn`` is true for a call recorded by a turn that
did not finish: the model provider failed, the turn hit its request limit, or a tool
raised something the turn could not recover from. Those calls ran, and the audit
trail is where "what did the assistant do?" is answered, so they are kept. The turn
itself is not saved, so such a call has no answer in the transcript, and the
transcript attributes a call to an answer by when it was written. The flag keeps a
failed turn's calls from being shown under the next answer.

NOT NULL with a false default: every existing row came from a turn that was saved.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("assistant_tool_calls") as batch:
        batch.add_column(
            sa.Column("from_failed_turn", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("assistant_tool_calls") as batch:
        batch.drop_column("from_failed_turn")
