"""Access audit log

Revision ID: 0065
Revises: 0064
Create Date: 2026-11-17

``audit_events`` records each access change, sign-in and authorization denial,
written in the same transaction as the change. It has no foreign keys and copies
the names it mentions, so a row outlives what it is about.

On Postgres, ``txid`` defaults to the writing transaction's id, and a trigger
makes the table append-only: UPDATE and TRUNCATE are refused, and DELETE unless
the transaction set ``duckhaven.audit_purge`` to ``on``.

The table is new, so the migration changes no existing rows.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB, "postgresql")

_POSTGRES_UPGRADE = (
    "ALTER TABLE audit_events ALTER COLUMN txid SET DEFAULT pg_current_xact_id()::text::bigint",
    """
    CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' AND current_setting('duckhaven.audit_purge', true) = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION USING
            MESSAGE = 'audit_events is append-only: ' || TG_OP || ' refused',
            ERRCODE = 'insufficient_privilege';
    END
    $$
    """,
    """
    CREATE TRIGGER audit_events_append_only_rows
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()
    """,
    """
    CREATE TRIGGER audit_events_append_only_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()
    """,
)

_INDEXES = {
    "ix_audit_events_time": ["event_time", "id"],
    "ix_audit_events_actor": ["actor_id", "event_time"],
    "ix_audit_events_subject": ["subject_id", "event_time"],
    "ix_audit_events_workspace": ["workspace_id", "event_time"],
    "ix_audit_events_action": ["action", "event_time"],
    "ix_audit_events_txid": ["txid"],
}


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "event_time", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("event_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="success"),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_email", sa.String(255), nullable=True),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("on_behalf_of_id", sa.Uuid(), nullable=True),
        sa.Column("on_behalf_of_email", sa.String(255), nullable=True),
        sa.Column("auth_method", sa.String(16), nullable=True),
        sa.Column("credential_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("source_ip", sa.String(64), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("workspace_slug", sa.String(255), nullable=True),
        sa.Column("target_type", sa.String(32), nullable=True),
        sa.Column("target_id", sa.String(255), nullable=True),
        sa.Column("target_name", sa.String(1024), nullable=True),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("subject_email", sa.String(255), nullable=True),
        sa.Column("self_change", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("before", _JSON, nullable=True),
        sa.Column("after", _JSON, nullable=True),
        sa.Column("details", _JSON, nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("txid", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            _in("category", ("identity", "access", "credential", "auth", "authz")),
            name="ck_audit_events_category",
        ),
        sa.CheckConstraint(
            _in("outcome", ("success", "denied", "failure")), name="ck_audit_events_outcome"
        ),
        sa.CheckConstraint(
            _in("actor_type", ("user", "service_account", "system", "idp", "anonymous")),
            name="ck_audit_events_actor_type",
        ),
        sa.CheckConstraint(
            "auth_method IS NULL OR " + _in("auth_method", ("session", "pat", "system")),
            name="ck_audit_events_auth_method",
        ),
    )
    for name, columns in _INDEXES.items():
        op.create_index(name, "audit_events", columns)
    op.create_index(
        "ix_audit_events_self_change",
        "audit_events",
        ["event_time"],
        postgresql_where=sa.text("self_change"),
        sqlite_where=sa.text("self_change"),
    )
    if op.get_bind().dialect.name == "postgresql":
        for statement in _POSTGRES_UPGRADE:
            op.execute(statement)


def downgrade() -> None:
    # Dropping the table drops its triggers; the function is left behind otherwise.
    op.drop_index("ix_audit_events_self_change", table_name="audit_events")
    for name in reversed(_INDEXES):
        op.drop_index(name, table_name="audit_events")
    op.drop_table("audit_events")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS audit_events_append_only()")
