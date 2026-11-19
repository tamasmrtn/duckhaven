"""The system catalog's own database: its tables, and the login agents read it with.

DuckHaven keeps a copy of the audit log, access, query history, lineage, catalog
metadata, compute, jobs and table health in a database of their own
(``duckhaven_system``), so an agent can be handed a login that reaches those
tables and nothing else. Agents never connect to ``duckhaven``, which holds users,
password hashes and session tokens.

``ensure()`` creates the schemas, the tables and the reader login, and re-applies
the login's password, limits, grants and row filters. It is idempotent and
serialized by an advisory lock, since replicas start together. Until it has
succeeded the copy is skipped; the publisher retries it each interval, so creating
the database later needs no restart.

The tables are a contract: columns are only ever added, never renamed, retyped or
removed. A column added to a table an existing store already has is added there by
``ensure()``, so it must be nullable.

**Row filters.** One login reads every table, so Postgres itself decides which
rows each query sees, with row-level security on every table. The agent attaches
with settings in its connection string (see ``duckhaven_shared.system_catalog``):
the workspace the query runs in, everyone it runs for, and the tables they may
read. A table's policy shows its rows only when it is among those tables, and a
table that every member reads (``permission`` None) narrows them further, to what
that member can see. Without the settings a query reads no row of any table.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass

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
from api.services.permissions import Permission
from duckhaven_shared.system_catalog import (
    SETTING_PRINCIPALS,
    SETTING_READABLE,
    SETTING_WORKSPACE,
)

logger = logging.getLogger(__name__)

ACCESS, QUERY, INFO, LINEAGE = "access", "query", "info_schema", "lineage"
COMPUTE, JOBS, STORAGE = "compute", "jobs", "storage"
# Private: copy cursors, and what the row filters consult.
STATE, ACL = "_state", "_acl"
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

# Who and what exists, replaced with access.grants.
USERS = sa.Table(
    "users",
    METADATA,
    sa.Column("user_id", _UUID, primary_key=True),
    sa.Column("email", sa.Text, nullable=False),
    sa.Column("name", sa.Text),
    sa.Column("principal_type", sa.Text, nullable=False),
    sa.Column("auth_provider", sa.Text, nullable=False),
    sa.Column("global_role", sa.Text, nullable=False),
    sa.Column("is_active", sa.Boolean, nullable=False),
    sa.Column("created_at", _TS),
    sa.Column("snapshot_time", _TS, nullable=False),
    schema=ACCESS,
)

WORKSPACES = sa.Table(
    "workspaces",
    METADATA,
    sa.Column("workspace_id", _UUID, primary_key=True),
    sa.Column("workspace_slug", sa.Text, nullable=False),
    sa.Column("name", sa.Text),
    sa.Column("description", sa.Text),
    sa.Column("timezone", sa.Text),
    sa.Column("default_agent_id", _UUID),
    sa.Column("default_agent_name", sa.Text),
    sa.Column("result_cache_enabled", sa.Boolean),
    sa.Column("created_at", _TS),
    sa.Column("snapshot_time", _TS, nullable=False),
    schema=ACCESS,
)

# Personal access tokens: who holds one, since when and until when. Never the secret
# or its hash.
CREDENTIALS = sa.Table(
    "credentials",
    METADATA,
    sa.Column("credential_id", _UUID, primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("principal_type", sa.Text, nullable=False),
    sa.Column("principal_id", _UUID, nullable=False),
    sa.Column("principal_email", sa.Text),
    sa.Column("principal_active", sa.Boolean),
    sa.Column("created_at", _TS),
    sa.Column("expires_at", _TS),
    sa.Column("snapshot_time", _TS, nullable=False),
    schema=ACCESS,
)

# One row per table a finished query read or wrote, worked out from its SQL as it
# was copied into query.history.
TABLE_ACCESS = sa.Table(
    "table_access",
    METADATA,
    sa.Column("query_id", _UUID, primary_key=True),
    sa.Column("catalog_name", sa.Text, primary_key=True),
    sa.Column("schema_name", sa.Text, primary_key=True),
    sa.Column("table_name", sa.Text, primary_key=True),
    sa.Column("access", sa.Text, primary_key=True),
    sa.Column("catalog_id", _UUID),
    sa.Column("finished_at", _TS, nullable=False),
    sa.Column("workspace_id", _UUID, nullable=False),
    sa.Column("workspace_slug", sa.Text),
    sa.Column("user_id", _UUID),
    sa.Column("user_email", sa.Text),
    sa.Column("on_behalf_of_id", _UUID),
    sa.Column("on_behalf_of_email", sa.Text),
    sa.Index("table_access_finished_at_idx", "finished_at"),
    sa.Index("table_access_table_idx", "catalog_name", "schema_name", "table_name", "finished_at"),
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
    sa.Column("active_catalog", sa.Text),
    sa.Column("active_schema", sa.Text),
    sa.Column("cache_status", sa.Text),
    sa.Column("result_source_query_id", _UUID),
    sa.Column("cpu_time_ms", sa.BigInteger),
    sa.Column("bytes_read", sa.BigInteger),
    sa.Column("peak_memory_bytes", sa.BigInteger),
    sa.Column("spill_bytes", sa.BigInteger),
    sa.Index("history_finished_at_idx", "finished_at"),
    sa.Index("history_user_idx", "user_id", "finished_at"),
    sa.Index("history_workspace_idx", "workspace_id", "finished_at"),
    schema=QUERY,
)

SESSIONS = sa.Table(
    "sessions",
    METADATA,
    sa.Column("session_id", _UUID, primary_key=True),
    sa.Column("workspace_id", _UUID, nullable=False),
    sa.Column("workspace_slug", sa.Text),
    sa.Column("user_id", _UUID),
    sa.Column("user_email", sa.Text),
    sa.Column("agent_id", _UUID),
    sa.Column("agent_name", sa.Text),
    sa.Column("runtime_id", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("close_reason", sa.Text),
    sa.Column("error", sa.Text),
    sa.Column("client_name", sa.Text),
    sa.Column("client_version", sa.Text),
    sa.Column("active_catalog", sa.Text),
    sa.Column("use_cache", sa.Boolean, nullable=False),
    sa.Column("created_at", _TS, nullable=False),
    sa.Column("opened_at", _TS),
    sa.Column("last_active_at", _TS, nullable=False),
    sa.Column("closed_at", _TS),
    sa.Index("sessions_created_at_idx", "created_at"),
    schema=QUERY,
)


# ── jobs: scheduled work, replaced every minute ───────────────────────────────

SCHEDULES = sa.Table(
    "schedules",
    METADATA,
    sa.Column("schedule_id", _UUID, primary_key=True),
    sa.Column("workspace_id", _UUID, nullable=False),
    sa.Column("workspace_slug", sa.Text),
    sa.Column("job_type", sa.Text, nullable=False),
    sa.Column("saved_query_id", _UUID),
    sa.Column("saved_query_name", sa.Text),
    sa.Column("agent_id", _UUID),
    sa.Column("agent_name", sa.Text),
    sa.Column("cron", sa.Text, nullable=False),
    sa.Column("timezone", sa.Text),
    sa.Column("enabled", sa.Boolean, nullable=False),
    sa.Column("next_run_at", _TS),
    sa.Column("last_run_at", _TS),
    sa.Column("last_run_query_id", _UUID),
    sa.Column("last_run_status", sa.Text),
    sa.Column("created_by_id", _UUID),
    sa.Column("created_by_email", sa.Text),
    sa.Column("created_at", _TS),
    sa.Column("snapshot_time", _TS, nullable=False),
    schema=JOBS,
)


# ── compute: agents now, and what each did, kept past the source's own week ───

AGENTS = sa.Table(
    "agents",
    METADATA,
    sa.Column("agent_id", _UUID, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("provider", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("lifecycle", sa.Text),
    sa.Column("access_mode", sa.Text, nullable=False),
    sa.Column("runtime_id", sa.Text),
    sa.Column("duckdb_version", sa.Text),
    sa.Column("agent_version", sa.Text),
    sa.Column("cores", sa.Integer),
    sa.Column("memory_limit_gb", sa.Float),
    sa.Column("requested_cpu", sa.Float),
    sa.Column("requested_memory_gb", sa.Float),
    sa.Column("idle_timeout_s", sa.Float),
    sa.Column("max_timeout_s", sa.Float),
    sa.Column("created_at", _TS),
    sa.Column("provisioned_at", _TS),
    sa.Column("terminated_at", _TS),
    sa.Column("last_ping_at", _TS),
    sa.Column("last_active_at", _TS),
    sa.Column("snapshot_time", _TS, nullable=False),
    schema=COMPUTE,
)

AGENT_EVENTS = sa.Table(
    "agent_events",
    METADATA,
    sa.Column("event_id", _UUID, primary_key=True),
    sa.Column("agent_id", _UUID, nullable=False),
    sa.Column("agent_name", sa.Text),
    sa.Column("event", sa.Text, nullable=False),
    sa.Column("reason", sa.Text),
    sa.Column("event_time", _TS, nullable=False),
    sa.Index("agent_events_time_idx", "event_time"),
    sa.Index("agent_events_agent_idx", "agent_id", "event_time"),
    schema=COMPUTE,
)

AGENT_UTILIZATION = sa.Table(
    "agent_utilization",
    METADATA,
    sa.Column("agent_id", _UUID, primary_key=True),
    sa.Column("minute", _TS, primary_key=True),
    sa.Column("agent_name", sa.Text),
    sa.Column("cpu_avg_percent", sa.Float, nullable=False),
    sa.Column("cpu_max_percent", sa.Float, nullable=False),
    sa.Column("memory_avg_percent", sa.Float, nullable=False),
    sa.Column("memory_max_percent", sa.Float, nullable=False),
    sa.Column("running_queries_max", sa.Integer, nullable=False),
    sa.Column("queued_queries_max", sa.Integer, nullable=False),
    sa.Column("sessions_max", sa.Integer, nullable=False),
    sa.Column("sample_count", sa.Integer, nullable=False),
    sa.Column("covered_seconds", sa.Float),
    sa.Column("oom_kills", sa.Integer),
    sa.Index("agent_utilization_minute_idx", "minute"),
    schema=COMPUTE,
)


# ── storage: table health over time, and the maintenance run on it ────────────

TABLE_HEALTH = sa.Table(
    "table_health",
    METADATA,
    sa.Column("sample_id", _UUID, primary_key=True),
    sa.Column("scanned_at", _TS, nullable=False),
    sa.Column("catalog_id", _UUID, nullable=False),
    sa.Column("catalog_name", sa.Text),
    sa.Column("workspace_id", _UUID),
    sa.Column("workspace_slug", sa.Text),
    sa.Column("schema_name", sa.Text, nullable=False),
    sa.Column("table_name", sa.Text, nullable=False),
    sa.Column("score", sa.Integer),
    sa.Column("snapshot_count", sa.Integer),
    sa.Column("snapshot_id", sa.BigInteger),
    sa.Column("oldest_snapshot_age_days", sa.Float),
    sa.Column("data_file_count", sa.Integer),
    sa.Column("manifest_count", sa.Integer),
    sa.Column("total_data_bytes", sa.BigInteger),
    sa.Column("avg_file_bytes", sa.BigInteger),
    sa.Column("metadata_bytes", sa.BigInteger),
    sa.Column("orphan_bytes", sa.BigInteger),
    sa.Column("orphan_file_count", sa.Integer),
    sa.Column("small_file_ratio", sa.Float),
    sa.Column("factors", postgresql.JSONB),
    sa.Index("table_health_scanned_at_idx", "scanned_at"),
    sa.Index("table_health_table_idx", "catalog_id", "schema_name", "table_name", "scanned_at"),
    schema=STORAGE,
)

MAINTENANCE_OPERATIONS = sa.Table(
    "maintenance_operations",
    METADATA,
    sa.Column("query_id", _UUID, primary_key=True),
    sa.Column("recommendation_id", _UUID),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("catalog_id", _UUID, nullable=False),
    sa.Column("catalog_name", sa.Text),
    sa.Column("workspace_id", _UUID),
    sa.Column("workspace_slug", sa.Text),
    sa.Column("schema_name", sa.Text, nullable=False),
    sa.Column("table_name", sa.Text, nullable=False),
    sa.Column("status", sa.Text),
    sa.Column("error", sa.Text),
    sa.Column("result", postgresql.JSONB),
    sa.Column("started_at", _TS),
    sa.Column("finished_at", _TS),
    sa.Column("applied_by_id", _UUID),
    sa.Column("applied_by_email", sa.Text),
    sa.Index("maintenance_operations_started_at_idx", "started_at"),
    schema=STORAGE,
)


# ── lineage: which tables and columns feed which, replaced every few minutes ───

TABLE_LINEAGE = sa.Table(
    "table_lineage",
    METADATA,
    sa.Column("edge_id", _UUID, primary_key=True),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("operation", sa.Text),
    sa.Column("confidence", sa.Text, nullable=False),
    sa.Column("column_lineage", sa.Text, nullable=False),
    sa.Column("source_catalog_id", _UUID),
    sa.Column("source_catalog_name", sa.Text),
    sa.Column("source_system", sa.Text),
    sa.Column("source_schema", sa.Text, nullable=False),
    sa.Column("source_table", sa.Text, nullable=False),
    sa.Column("target_catalog_id", _UUID),
    sa.Column("target_catalog_name", sa.Text),
    sa.Column("target_system", sa.Text),
    sa.Column("target_schema", sa.Text, nullable=False),
    sa.Column("target_table", sa.Text, nullable=False),
    sa.Column("workspace_id", _UUID),
    sa.Column("workspace_slug", sa.Text),
    sa.Column("last_query_id", _UUID),
    sa.Column("first_seen_at", _TS, nullable=False),
    sa.Column("last_seen_at", _TS, nullable=False),
    sa.Column("observation_count", sa.Integer, nullable=False),
    sa.Column("snapshot_time", _TS, nullable=False),
    schema=LINEAGE,
)

COLUMN_LINEAGE = sa.Table(
    "column_lineage",
    METADATA,
    sa.Column("id", _UUID, primary_key=True),
    sa.Column("edge_id", _UUID, nullable=False),
    sa.Column("source_catalog_id", _UUID),
    sa.Column("source_catalog_name", sa.Text),
    sa.Column("source_system", sa.Text),
    sa.Column("source_schema", sa.Text, nullable=False),
    sa.Column("source_table", sa.Text, nullable=False),
    sa.Column("source_column", sa.Text, nullable=False),
    sa.Column("target_catalog_id", _UUID),
    sa.Column("target_catalog_name", sa.Text),
    sa.Column("target_system", sa.Text),
    sa.Column("target_schema", sa.Text, nullable=False),
    sa.Column("target_table", sa.Text, nullable=False),
    sa.Column("target_column", sa.Text, nullable=False),
    sa.Column("first_seen_at", _TS, nullable=False),
    sa.Column("last_seen_at", _TS, nullable=False),
    sa.Column("snapshot_time", _TS, nullable=False),
    sa.Index("column_lineage_edge_idx", "edge_id"),
    schema=LINEAGE,
)


# ── info_schema: what each member can see of their workspace's catalogs ────────

# One row per catalog attachment; refreshed with access.grants.
INFO_CATALOGS = sa.Table(
    "catalogs",
    METADATA,
    sa.Column("workspace_id", _UUID, primary_key=True),
    sa.Column("catalog_id", _UUID, primary_key=True),
    sa.Column("catalog_name", sa.Text, nullable=False),
    sa.Column("catalog_kind", sa.Text, nullable=False),
    sa.Column("access_mode", sa.Text, nullable=False),
    sa.Column("is_default", sa.Boolean, nullable=False),
    sa.Column("created_at", _TS),
    sa.Column("attached_at", _TS),
    schema=INFO,
)

# The rest, one catalog at a time, from each catalog's own metadata.
INFO_SCHEMATA = sa.Table(
    "schemata",
    METADATA,
    sa.Column("catalog_id", _UUID, primary_key=True),
    sa.Column("catalog_name", sa.Text, nullable=False),
    sa.Column("schema_name", sa.Text, primary_key=True),
    sa.Column("refreshed_at", _TS, nullable=False),
    schema=INFO,
)

INFO_TABLES = sa.Table(
    "tables",
    METADATA,
    sa.Column("catalog_id", _UUID, primary_key=True),
    sa.Column("catalog_name", sa.Text, nullable=False),
    sa.Column("schema_name", sa.Text, primary_key=True),
    sa.Column("table_name", sa.Text, primary_key=True),
    sa.Column("table_type", sa.Text, nullable=False),
    sa.Column("table_format", sa.Text, nullable=False),
    sa.Column("column_count", sa.Integer, nullable=False),
    sa.Column("row_count", sa.BigInteger),
    sa.Column("bytes", sa.BigInteger),
    sa.Column("table_owner", sa.Text),
    sa.Column("last_altered", _TS),
    sa.Column("refreshed_at", _TS, nullable=False),
    schema=INFO,
)

INFO_COLUMNS = sa.Table(
    "columns",
    METADATA,
    sa.Column("catalog_id", _UUID, primary_key=True),
    sa.Column("catalog_name", sa.Text, nullable=False),
    sa.Column("schema_name", sa.Text, primary_key=True),
    sa.Column("table_name", sa.Text, primary_key=True),
    sa.Column("column_name", sa.Text, primary_key=True),
    sa.Column("ordinal_position", sa.Integer, nullable=False),
    sa.Column("data_type", sa.Text, nullable=False),
    sa.Column("source_type", sa.Text, nullable=False),
    sa.Column("is_nullable", sa.Text, nullable=False),
    schema=INFO,
)

# ── _acl: what the row filters consult, refreshed with access.grants ──────────
# Private: the reader reaches it only through `_acl.visible()`.

ACL_ATTACHMENTS = sa.Table(
    "attachments",
    METADATA,
    sa.Column("workspace_id", _UUID, primary_key=True),
    sa.Column("catalog_id", _UUID, primary_key=True),
    sa.Column("access_mode", sa.Text, nullable=False),
    schema=ACL,
)

ACL_MEMBERS = sa.Table(
    "members",
    METADATA,
    sa.Column("workspace_id", _UUID, primary_key=True),
    sa.Column("user_id", _UUID, primary_key=True),
    schema=ACL,
)

# Every catalog grant, of any tier: any of them makes what it covers visible.
ACL_SCOPES = sa.Table(
    "scopes",
    METADATA,
    sa.Column("user_id", _UUID, nullable=False),
    sa.Column("catalog_id", _UUID, nullable=False),
    sa.Column("schema_name", sa.Text),
    sa.Column("table_name", sa.Text),
    # Each of `_acl.visible()`'s coverage tests is one probe of this.
    sa.Index("scopes_lookup_idx", "user_id", "catalog_id", "schema_name", "table_name"),
    schema=ACL,
)


# What a row of info_schema must be among `_acl.visible()`'s keys to be seen: the
# catalog, its schema or the table itself. A schema is also seen for a grant on
# one of its tables (`n:`), as the catalog tree shows it.
def _visible(*keys: str) -> str:
    return " OR ".join(f"{key} IN (SELECT {ACL}.visible())" for key in keys)


_CATALOG_KEY = "format('c:%s', catalog_id)"
_SCHEMA_KEY = "format('s:%s.%I', catalog_id, schema_name)"
_NESTED_KEY = "format('n:%s.%I', catalog_id, schema_name)"
_TABLE_KEY = "format('t:%s.%I.%I', catalog_id, schema_name, table_name)"
_IN_WORKSPACE = (
    f"workspace_id = (SELECT {ACL}.workspace()) AND (SELECT cardinality({ACL}.principals()) > 0)"
)
_SCHEMA_VISIBLE = _visible(_CATALOG_KEY, _SCHEMA_KEY, _NESTED_KEY)
_TABLE_VISIBLE = _visible(_CATALOG_KEY, _SCHEMA_KEY, _TABLE_KEY)


@dataclass(frozen=True)
class SystemTable:
    """A table queries may read, and what reading it takes."""

    table: sa.Table
    description: str
    # The permission reading it takes, held by everyone a query runs for. None for
    # a table every member of the workspace reads, which then needs `row_filter`.
    permission: str | None
    # A further SQL condition on its rows, for a table every member reads: the
    # rows of what this member can see. Its names resolve against the table.
    row_filter: str | None = None

    @property
    def schema(self) -> str:
        return str(self.table.schema)

    @property
    def name(self) -> str:
        return self.table.name

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"


# The tables a query may read, in the order the catalog tree lists them.
TABLES: tuple[SystemTable, ...] = (
    SystemTable(
        INFO_CATALOGS, "The catalogs attached to this workspace", None, row_filter=_IN_WORKSPACE
    ),
    SystemTable(
        INFO_SCHEMATA,
        "The schemas you can see in this workspace's catalogs",
        None,
        row_filter=_SCHEMA_VISIBLE,
    ),
    SystemTable(
        INFO_TABLES,
        "The tables you can see, with their size and last write",
        None,
        row_filter=_TABLE_VISIBLE,
    ),
    SystemTable(
        INFO_COLUMNS,
        "The columns of the tables you can see, with the type a query sees",
        None,
        row_filter=_TABLE_VISIBLE,
    ),
    SystemTable(AUDIT, "Every event of the access audit log", Permission.AUDIT_READ.value),
    SystemTable(
        GRANTS, "Current access, one row per grant of any kind", Permission.AUDIT_READ.value
    ),
    SystemTable(USERS, "Every user and service account", Permission.AUDIT_READ.value),
    SystemTable(WORKSPACES, "Every workspace", Permission.AUDIT_READ.value),
    SystemTable(
        CREDENTIALS,
        "Personal access tokens: who holds each, and until when",
        Permission.AUDIT_READ.value,
    ),
    SystemTable(
        TABLE_ACCESS, "Each table a finished query read or wrote", Permission.AUDIT_READ.value
    ),
    SystemTable(HISTORY, "Finished queries", Permission.QUERIES_ADMIN.value),
    SystemTable(SESSIONS, "SQL sessions, open and ended", Permission.QUERIES_ADMIN.value),
    SystemTable(
        TABLE_LINEAGE, "Which tables feed which, and how often", Permission.AUDIT_READ.value
    ),
    SystemTable(COLUMN_LINEAGE, "Which columns feed which", Permission.AUDIT_READ.value),
    SystemTable(SCHEDULES, "Scheduled jobs, and how each last ran", Permission.QUERIES_ADMIN.value),
    SystemTable(AGENTS, "Every agent, and its state now", Permission.AGENTS_MANAGE.value),
    SystemTable(
        AGENT_EVENTS,
        "Each time an agent started, connected, disconnected or stopped",
        Permission.AGENTS_MANAGE.value,
    ),
    SystemTable(
        AGENT_UTILIZATION,
        "Each agent's CPU, memory and queue, minute by minute",
        Permission.AGENTS_MANAGE.value,
    ),
    SystemTable(
        TABLE_HEALTH,
        "Each table's health at each maintenance scan",
        Permission.MAINTENANCE_MANAGE.value,
    ),
    SystemTable(
        MAINTENANCE_OPERATIONS,
        "Each maintenance run on a table, and its outcome",
        Permission.MAINTENANCE_MANAGE.value,
    ),
)
# The schemas queries may read, in the order of their first table.
SCHEMAS: tuple[str, ...] = tuple(dict.fromkeys(t.schema for t in TABLES))
ALL_SCHEMAS: tuple[str, ...] = (*SCHEMAS, STATE, ACL)

# Each column type as an agent's DuckDB reports it through the `postgres` extension,
# which reads JSONB as text.
_DUCKDB_TYPES: dict[type, str] = {
    postgresql.UUID: "UUID",
    sa.DateTime: "TIMESTAMP WITH TIME ZONE",
    sa.Integer: "INTEGER",
    sa.BigInteger: "BIGINT",
    sa.Float: "DOUBLE",
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

# When each catalog was last read whole into info_schema, and what has changed in
# one since (`metadata`). Private like the cursors.
METADATA_READS = sa.Table(
    "metadata_reads",
    METADATA,
    sa.Column("catalog_id", _UUID, primary_key=True),
    sa.Column("read_at", _TS, nullable=False),
    schema=STATE,
)

METADATA_CHANGES = sa.Table(
    "metadata_changes",
    METADATA,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("catalog_id", _UUID, nullable=False),
    # A table's change, or the whole catalog's when both are null.
    sa.Column("schema_name", sa.Text),
    sa.Column("table_name", sa.Text),
    sa.Column("marked_at", _TS, nullable=False, server_default=sa.func.now()),
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
            for schema in ALL_SCHEMAS:
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            await conn.run_sync(METADATA.create_all)
            await _add_missing_columns(conn)
            # Read under the lock, so two replicas never mint two passwords.
            async with session_factory() as db:
                password = await reader_password(db)
            await _apply_reader_login(conn, password)
            await _apply_row_filters(conn)
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
    readable = ", ".join(SCHEMAS)
    await conn.execute(text(f"GRANT USAGE ON SCHEMA {readable}, {ACL} TO {READER_ROLE}"))
    await conn.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {readable} TO {READER_ROLE}"))
    # The row filters call `_acl`'s functions as the reader; `visible()` reads its
    # tables as their owner, so the reader holds nothing on them itself.
    await conn.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA {ACL} FROM {READER_ROLE}"))


async def _add_missing_columns(conn: AsyncConnection) -> None:
    """Add the columns and indexes a table has gained since the store was created.

    ``create_all`` creates only missing tables. The tables only ever gain nullable
    columns, so adding one is all an existing store needs.
    """
    for table in METADATA.sorted_tables:
        existing = set(
            await conn.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = :table"
                ),
                {"schema": table.schema, "table": table.name},
            )
        )
        for column in table.columns:
            if column.name not in existing:
                kind = column.type.compile(dialect=conn.dialect)
                await conn.execute(
                    text(
                        f"ALTER TABLE {table.schema}.{table.name} "
                        f'ADD COLUMN IF NOT EXISTS "{column.name}" {kind}'
                    )
                )
        for index in table.indexes:
            await conn.run_sync(lambda sync, index=index: index.create(sync, checkfirst=True))


def _setting(name: str) -> str:
    return f"nullif(current_setting('{name}', true), '')"


# What a query runs for, read from its connection's settings. Unset reads as
# nothing: no workspace, no one, no table.
_ROW_FILTER_FUNCTIONS = {
    "readable() RETURNS text[]": (
        f"SELECT coalesce(string_to_array({_setting(SETTING_READABLE)}, ','), '{{}}')"
    ),
    "workspace() RETURNS uuid": f"SELECT {_setting(SETTING_WORKSPACE)}::uuid",
    "principals() RETURNS uuid[]": (
        f"SELECT coalesce(string_to_array({_setting(SETTING_PRINCIPALS)}, ',')::uuid[], '{{}}')"
    ),
}


# The keys of what everyone the query runs for may see in the workspace's catalogs,
# for the info_schema filters: `c:` a whole catalog, `s:` a schema, `t:` a table,
# and `n:` a schema seen for a table in it. It mirrors `services.grants`: an open
# attachment shows every member everything; a scoped one shows what a grant of
# any tier covers, for a member, and a query run for someone else shows only
# what both may see. With several principals, a grant's node is in it when each
# of them holds a grant on it or above it: the deepest grant on a path that all
# of them cover is one of theirs, so testing each of their grants finds it.
_VISIBLE = f"""
WITH ps AS (
  SELECT ids, (SELECT count(DISTINCT x) FROM unnest(ids) x) AS n
  FROM (SELECT {ACL}.principals() AS ids) p
),
attached AS (
  SELECT a.catalog_id, a.access_mode
  FROM {ACL}.attachments a, ps
  WHERE a.workspace_id = {ACL}.workspace() AND ps.n > 0
),
members AS (
  SELECT count(DISTINCT m.user_id) = (SELECT n FROM ps) AS all_in
  FROM {ACL}.members m, ps
  WHERE m.workspace_id = {ACL}.workspace() AND m.user_id = ANY (ps.ids)
),
candidates AS (
  SELECT DISTINCT k.catalog_id, k.schema_name, k.table_name
  FROM {ACL}.scopes k
  JOIN attached a ON a.catalog_id = k.catalog_id AND a.access_mode = 'scoped', ps, members
  WHERE members.all_in AND k.user_id = ANY (ps.ids)
),
covered AS (
  SELECT k.* FROM candidates k, ps
  WHERE ps.n = 1 OR NOT EXISTS (
    SELECT 1 FROM unnest(ps.ids) p(id)
    WHERE NOT EXISTS (
        SELECT 1 FROM {ACL}.scopes g
        WHERE g.user_id = p.id AND g.catalog_id = k.catalog_id AND g.schema_name IS NULL)
      AND NOT EXISTS (
        SELECT 1 FROM {ACL}.scopes g
        WHERE g.user_id = p.id AND g.catalog_id = k.catalog_id
          AND g.schema_name = k.schema_name AND g.table_name IS NULL)
      AND NOT EXISTS (
        SELECT 1 FROM {ACL}.scopes g
        WHERE g.user_id = p.id AND g.catalog_id = k.catalog_id
          AND g.schema_name = k.schema_name AND g.table_name = k.table_name))
)
SELECT 'c:' || catalog_id FROM attached WHERE access_mode = 'open'
UNION ALL SELECT 'c:' || catalog_id FROM covered WHERE schema_name IS NULL
UNION ALL SELECT format('s:%s.%I', catalog_id, schema_name) FROM covered
  WHERE schema_name IS NOT NULL AND table_name IS NULL
UNION ALL SELECT format('t:%s.%I.%I', catalog_id, schema_name, table_name) FROM covered
  WHERE table_name IS NOT NULL
UNION ALL SELECT format('n:%s.%I', catalog_id, schema_name) FROM covered
  WHERE table_name IS NOT NULL
"""


def row_filter(table: SystemTable) -> str:
    """The condition a row of ``table`` must meet for a query to see it."""
    # A sub-select, so Postgres decides it once per query rather than per row.
    readable = f"(SELECT '{table.qualified}' = ANY ({ACL}.readable()))"
    return f"{readable} AND ({table.row_filter})" if table.row_filter else readable


async def _apply_row_filters(conn: AsyncConnection) -> None:
    """(Re)create each table's row-level security policy, and what it calls."""
    for signature, body in _ROW_FILTER_FUNCTIONS.items():
        await conn.execute(
            text(
                f"CREATE OR REPLACE FUNCTION {ACL}.{signature} "
                f"LANGUAGE sql STABLE AS $fn$ {body} $fn$"
            )
        )
    # As its owner, with a fixed search path, so the reader needs nothing on the
    # tables it reads and no schema of theirs can stand in for one of these.
    await conn.execute(
        text(
            f"CREATE OR REPLACE FUNCTION {ACL}.visible() RETURNS SETOF text "
            "LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp "
            f"AS $fn$ {_VISIBLE} $fn$"
        )
    )
    for table in TABLES:
        await conn.execute(text(f"ALTER TABLE {table.qualified} ENABLE ROW LEVEL SECURITY"))
        await conn.execute(text(f"DROP POLICY IF EXISTS readable ON {table.qualified}"))
        await conn.execute(
            text(
                f"CREATE POLICY readable ON {table.qualified} FOR SELECT "
                f"USING ({row_filter(table)})"
            )
        )
