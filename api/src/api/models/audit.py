"""The access audit log: one row per access change, sign-in or authorization denial.

Rows are written in the same transaction as the change they record, so a change
that rolls back leaves no row and one that commits always has one. They carry no
foreign keys and snapshot the names they mention (an email, a workspace slug), so
a row outlives the user, workspace or catalog it is about.

On Postgres the table is append-only: a trigger refuses UPDATE and TRUNCATE, and
DELETE unless the transaction set ``duckhaven.audit_purge`` to ``on``, which only
the retention purge does. The guard stops application bugs, not a database
administrator, who can drop the trigger.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DDL,
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    FetchedValue,
    Index,
    Integer,
    String,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base

# JSONB on Postgres, plain JSON on other dialects (SQLite under unit tests).
_Json = JSON().with_variant(JSONB, "postgresql")

CATEGORIES = ("identity", "access", "credential", "auth", "authz")
OUTCOMES = ("success", "denied", "failure")
#: Who acted. ``idp`` is a sign-in rewriting an account from its identity
#: provider; ``anonymous`` a request that never authenticated, such as a failed
#: sign-in.
ACTOR_TYPES = ("user", "service_account", "system", "idp", "anonymous")
AUTH_METHODS = ("session", "pat", "system")

#: The transaction setting under which the retention purge may delete rows.
PURGE_SETTING = "duckhaven.audit_purge"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _now() -> datetime:
    return datetime.now(tz=UTC)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Set in Python as well, so the log line emitted after commit carries the
    # same time as the row.
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    #: Bumped only if a field's meaning changes; fields are only ever added.
    event_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="success")

    # Who acted. Acting for someone (the assistant), the person is on_behalf_of.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    on_behalf_of_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    on_behalf_of_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: The credential the request presented: its id, never its secret.
    credential_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Where the request came from.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # What it was about.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    workspace_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: A readable name for the target, such as ``sales.analytics.orders``.
    target_name: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: Whose access changed, when that is a person or service account.
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    subject_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The subject is the actor, or the person the actor acted for.
    self_change: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    before: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    after: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    details: Mapped[dict | None] = mapped_column(_Json, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: The writing transaction's id (Postgres only; set by a server default added
    #: in DDL), which lets a reader copy rows exactly once in commit order.
    #: ``FetchedValue`` tells the ORM the database fills it in; without it an
    #: insert sends NULL and the default never applies.
    txid: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, server_default=FetchedValue()
    )

    __table_args__ = (
        CheckConstraint(_in("category", CATEGORIES), name="ck_audit_events_category"),
        CheckConstraint(_in("outcome", OUTCOMES), name="ck_audit_events_outcome"),
        CheckConstraint(_in("actor_type", ACTOR_TYPES), name="ck_audit_events_actor_type"),
        CheckConstraint(
            f"auth_method IS NULL OR {_in('auth_method', AUTH_METHODS)}",
            name="ck_audit_events_auth_method",
        ),
        Index("ix_audit_events_time", "event_time", "id"),
        Index("ix_audit_events_actor", "actor_id", "event_time"),
        Index("ix_audit_events_subject", "subject_id", "event_time"),
        Index("ix_audit_events_workspace", "workspace_id", "event_time"),
        Index("ix_audit_events_action", "action", "event_time"),
        Index("ix_audit_events_txid", "txid"),
        Index(
            "ix_audit_events_self_change",
            "event_time",
            postgresql_where=text("self_change"),
            sqlite_where=text("self_change"),
        ),
    )


# The Postgres-only pieces, as statements shared by migration 0065 and the
# `create_all` that integration tests build their schema with. Without the
# listener below, a test database would silently lack the guard and the default.
POSTGRES_DDL: tuple[str, ...] = (
    "ALTER TABLE audit_events ALTER COLUMN txid SET DEFAULT pg_current_xact_id()::text::bigint",
    f"""
    CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' AND current_setting('{PURGE_SETTING}', true) = 'on' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION USING
            MESSAGE = 'audit_events is append-only: ' || TG_OP || ' refused',
            ERRCODE = 'insufficient_privilege';
    END
    $$
    """,
    "DROP TRIGGER IF EXISTS audit_events_append_only_rows ON audit_events",
    """
    CREATE TRIGGER audit_events_append_only_rows
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only()
    """,
    "DROP TRIGGER IF EXISTS audit_events_append_only_truncate ON audit_events",
    """
    CREATE TRIGGER audit_events_append_only_truncate
    BEFORE TRUNCATE ON audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION audit_events_append_only()
    """,
)

for _statement in POSTGRES_DDL:
    event.listen(
        AuditEvent.__table__, "after_create", DDL(_statement).execute_if(dialect="postgresql")
    )
