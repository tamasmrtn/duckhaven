"""The system catalog's own database: its tables, and the login agents read it with.

DuckHaven keeps a copy of the audit log, current access and query history in a
database of their own (``duckhaven_system``), so an agent can be handed a login
that reaches those tables and nothing else. Agents never connect to ``duckhaven``,
which holds users, password hashes and session tokens.

``ensure()`` creates the schemas, the tables and the reader login, and re-applies
the login's password, limits and grants. It is idempotent and serialized by an
advisory lock, since replicas start together. Until it has succeeded the copy is
skipped; the publisher retries it each interval, so creating the database later
needs no restart.

The tables are a contract: columns are only ever added, never renamed, retyped or
removed.
"""

from __future__ import annotations

import logging
import secrets

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from api.config import settings
from api.db.entra import attach_entra_auth
from api.db.session import engine_kwargs
from api.models.user import Credential

logger = logging.getLogger(__name__)

ACCESS, QUERY, STATE = "access", "query", "_state"
READER_ROLE = "dh_system_reader"
# The reader's password, in the control-plane database like the DuckLake logins'.
READER_CREDENTIAL_KIND = "system_reader"
# A read can run this long, and a connection sit idle this long, before Postgres
# ends it; the connection limit is a setting.
_READER_STATEMENT_TIMEOUT = "5min"
_READER_IDLE_SESSION_TIMEOUT = "10min"
# Distinct advisory-lock key in the "dhs" family ('dhse': system catalog ensure).
_ENSURE_LOCK_KEY = 0x64687365

_UUID = postgresql.UUID(as_uuid=True)
_TS = sa.DateTime(timezone=True)

METADATA = sa.MetaData()

AUDIT = sa.Table(
    "audit",
    METADATA,
    sa.Column("id", _UUID, primary_key=True),
    sa.Column("event_time", _TS, nullable=False),
    sa.Column("event_version", sa.Integer, nullable=False),
    sa.Column("category", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("outcome", sa.Text, nullable=False),
    sa.Column("actor_id", _UUID),
    sa.Column("actor_email", sa.Text),
    sa.Column("actor_type", sa.Text, nullable=False),
    sa.Column("on_behalf_of_id", _UUID),
    sa.Column("on_behalf_of_email", sa.Text),
    sa.Column("auth_method", sa.Text),
    sa.Column("credential_id", _UUID),
    sa.Column("request_id", sa.Text),
    sa.Column("source_ip", sa.Text),
    sa.Column("user_agent", sa.Text),
    sa.Column("workspace_id", _UUID),
    sa.Column("workspace_slug", sa.Text),
    sa.Column("target_type", sa.Text),
    sa.Column("target_id", sa.Text),
    sa.Column("target_name", sa.Text),
    sa.Column("subject_id", _UUID),
    sa.Column("subject_email", sa.Text),
    sa.Column("self_change", sa.Boolean, nullable=False),
    sa.Column("before", postgresql.JSONB),
    sa.Column("after", postgresql.JSONB),
    sa.Column("details", postgresql.JSONB),
    sa.Column("error_code", sa.Text),
    sa.Index("audit_event_time_idx", "event_time"),
    sa.Index("audit_actor_idx", "actor_id", "event_time"),
    sa.Index("audit_subject_idx", "subject_id", "event_time"),
    schema=ACCESS,
)

# Who can reach what, as one row per grant of any kind, replaced whole on each
# refresh. `kind` says which columns apply.
GRANTS = sa.Table(
    "grants",
    METADATA,
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("principal_type", sa.Text),
    sa.Column("principal_id", _UUID),
    sa.Column("principal_email", sa.Text),
    sa.Column("principal_active", sa.Boolean),
    sa.Column("workspace_id", _UUID),
    sa.Column("workspace_slug", sa.Text),
    sa.Column("catalog_id", _UUID),
    sa.Column("catalog_slug", sa.Text),
    sa.Column("schema_name", sa.Text),
    sa.Column("table_name", sa.Text),
    sa.Column("agent_id", _UUID),
    sa.Column("agent_name", sa.Text),
    sa.Column("level", sa.Text, nullable=False),
    sa.Column("granted_by_id", _UUID),
    sa.Column("granted_by_email", sa.Text),
    sa.Column("granted_at", _TS),
    sa.Column("snapshot_time", _TS, nullable=False),
    schema=ACCESS,
)

HISTORY = sa.Table(
    "history",
    METADATA,
    sa.Column("id", _UUID, primary_key=True),
    sa.Column("workspace_id", _UUID, nullable=False),
    sa.Column("workspace_slug", sa.Text),
    sa.Column("user_id", _UUID),
    sa.Column("user_email", sa.Text),
    sa.Column("on_behalf_of_id", _UUID),
    sa.Column("on_behalf_of_email", sa.Text),
    sa.Column("agent_id", _UUID),
    sa.Column("agent_name", sa.Text),
    sa.Column("runtime_id", sa.Text),
    sa.Column("origin", sa.Text),
    sa.Column("statement_type", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("error", sa.Text),
    sa.Column("sql", sa.Text, nullable=False),
    sa.Column("row_count", sa.BigInteger),
    sa.Column("duration_ms", sa.BigInteger),
    sa.Column("result_bytes", sa.BigInteger),
    sa.Column("started_at", _TS, nullable=False),
    sa.Column("dispatched_at", _TS),
    sa.Column("running_at", _TS),
    sa.Column("finished_at", _TS, nullable=False),
    sa.Column("session_id", _UUID),
    sa.Column("schedule_id", _UUID),
    sa.Column("client_name", sa.Text),
    sa.Column("client_version", sa.Text),
    sa.Column("tags", postgresql.JSONB),
    sa.Index("history_finished_at_idx", "finished_at"),
    sa.Index("history_user_idx", "user_id", "finished_at"),
    sa.Index("history_workspace_idx", "workspace_id", "finished_at"),
    schema=QUERY,
)

# The tables a query may read, in the order the catalog tree lists them.
READABLE = (AUDIT, GRANTS, HISTORY)

# Each column type as an agent's DuckDB reports it through the `postgres` extension,
# which reads JSONB as text.
_DUCKDB_TYPES: dict[type, str] = {
    postgresql.UUID: "UUID",
    sa.DateTime: "TIMESTAMP WITH TIME ZONE",
    sa.Integer: "INTEGER",
    sa.BigInteger: "BIGINT",
    sa.Boolean: "BOOLEAN",
    sa.Text: "VARCHAR",
    postgresql.JSONB: "VARCHAR",
}


def duckdb_type(column: sa.Column) -> str:
    """``column``'s type as a query on the system catalog sees it."""
    return _DUCKDB_TYPES[type(column.type)]


# Where each copy has got to. Private: the reader is granted nothing here.
CURSORS = sa.Table(
    "cursors",
    METADATA,
    sa.Column("name", sa.Text, primary_key=True),
    sa.Column("value", postgresql.JSONB, nullable=False),
    sa.Column("updated_at", _TS, nullable=False, server_default=sa.func.now()),
    schema=STATE,
)

_engine: AsyncEngine | None = None
# The last reason the store was unavailable, so a repeated failure logs once.
_unavailable_reason: str | None = None
_available = False
_ready_logged = False


def get_engine() -> AsyncEngine:
    """The owner-credential engine, configured like the control-plane one."""
    global _engine
    if _engine is None:
        url = settings.system_catalog_database_url
        _engine = create_async_engine(url, **engine_kwargs(url))
        if settings.db_auth_mode == "entra":
            attach_entra_auth(_engine)
    return _engine


def has_engine() -> bool:
    """Whether this process has opened the store (its publisher has started)."""
    return _engine is not None


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def is_available() -> bool:
    """Whether `ensure()` has succeeded since the store was last found broken."""
    return _available


def mark_stale() -> None:
    """Have the next publish re-run `ensure()`: the database was recreated, say."""
    global _available
    _available = False


async def reader_password(db: AsyncSession) -> str:
    """The reader login's password, created on first use."""
    credential = await db.scalar(
        sa.select(Credential).where(Credential.kind == READER_CREDENTIAL_KIND)
    )
    if credential is not None and credential.token:
        return credential.token
    # Kept before the commit, which may expire the row under the caller's factory.
    token = secrets.token_urlsafe(32)
    db.add(Credential(kind=READER_CREDENTIAL_KIND, token=token))
    await db.commit()
    return token


async def ensure(session_factory: async_sessionmaker[AsyncSession]) -> bool:
    """Create or repair the store; return whether it is usable.

    A failure is logged once per distinct reason, and leaves the store
    unavailable until a later call succeeds.
    """
    global _available, _unavailable_reason, _ready_logged
    if not settings.system_catalog_enabled:
        return False
    try:
        async with get_engine().begin() as conn:
            # Replicas start together; the second waits, then finds it all done.
            await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ENSURE_LOCK_KEY})
            for schema in (ACCESS, QUERY, STATE):
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            await conn.run_sync(METADATA.create_all)
            # Read under the lock, so two replicas never mint two passwords.
            async with session_factory() as db:
                password = await reader_password(db)
            await _apply_reader_login(conn, password)
    except Exception as exc:  # noqa: BLE001 - any failure leaves the store unavailable
        _available = False
        reason = f"{type(exc).__name__}: {exc}".splitlines()[0]
        if reason != _unavailable_reason:
            logger.warning(
                "System catalog unavailable (%s). Create its database with "
                "scripts/enable-system-catalog.sh; see docs/deployment/system-catalog.md. "
                "The audit log and query history are unaffected.",
                reason,
            )
            _unavailable_reason = reason
        return False
    # Once, and again on recovering from a logged failure; not on every repair.
    if not _ready_logged or _unavailable_reason is not None:
        logger.info("System catalog store ready")
        _ready_logged = True
    _available, _unavailable_reason = True, None
    return True


async def _apply_reader_login(conn: AsyncConnection, password: str) -> None:
    """Create the reader login, or bring an existing one back to its settings."""
    exists = await conn.scalar(
        text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": READER_ROLE}
    )
    # CREATE/ALTER ROLE take no bind parameters, so the password goes through a
    # transaction-local setting and is quoted server-side with format(%L), as
    # for the DuckLake logins.
    await conn.execute(
        text("SELECT set_config('duckhaven.role_password', :pw, true)"), {"pw": password}
    )
    verb = "ALTER" if exists else "CREATE"
    limit = int(settings.system_catalog_reader_connection_limit)
    await conn.execute(
        text(
            f"DO $do$ BEGIN EXECUTE format("
            f"'{verb} ROLE %I WITH LOGIN PASSWORD %L CONNECTION LIMIT {limit}', "
            f"'{READER_ROLE}', current_setting('duckhaven.role_password')); END $do$"
        )
    )
    # Defaults the login starts every session with. Read-only is a default it
    # could change; what stops writes is that it is granted SELECT and nothing else.
    for setting, value in (
        ("default_transaction_read_only", "on"),
        ("statement_timeout", _READER_STATEMENT_TIMEOUT),
        ("idle_session_timeout", _READER_IDLE_SESSION_TIMEOUT),
    ):
        await conn.execute(text(f"ALTER ROLE {READER_ROLE} SET {setting} = '{value}'"))
    await conn.execute(
        text(
            "DO $do$ BEGIN EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', "
            f"current_database(), '{READER_ROLE}'); END $do$"
        )
    )
    await conn.execute(text(f"GRANT USAGE ON SCHEMA {ACCESS}, {QUERY} TO {READER_ROLE}"))
    await conn.execute(
        text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {ACCESS}, {QUERY} TO {READER_ROLE}")
    )
