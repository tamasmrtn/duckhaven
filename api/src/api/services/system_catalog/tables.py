"""Schemas for the Iceberg tables exposed by the system catalog.

Each :class:`SystemTable` declares its namespace, name, write mode, and a
PyArrow schema (the single source of truth used both to create the Iceberg
table and to coerce mapped rows on write). Column choices mirror the
Postgres ``queries`` table plus cross-workspace context (slugs/names/emails);
see ``docs/reference/system-catalog.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa

from api.services.system_catalog.constants import (
    ACCESS_NAMESPACE,
    INFO_NAMESPACE,
    QUERY_NAMESPACE,
)


@dataclass(frozen=True)
class SystemTable:
    namespace: str
    name: str
    schema: pa.Schema
    # "append" for ever-growing history; "overwrite" for current-state snapshots.
    mode: str

    @property
    def identifier(self) -> tuple[str, str]:
        return (self.namespace, self.name)


_TS = pa.timestamp("us", tz="UTC")

QUERY_HISTORY = SystemTable(
    namespace=QUERY_NAMESPACE,
    name="history",
    mode="append",
    schema=pa.schema(
        [
            ("query_id", pa.string()),
            ("workspace_id", pa.string()),
            ("workspace_slug", pa.string()),
            ("agent_id", pa.string()),
            ("agent_name", pa.string()),
            ("user_id", pa.string()),
            ("user_email", pa.string()),
            ("statement_type", pa.string()),
            ("status", pa.string()),
            ("origin", pa.string()),
            ("row_count", pa.int64()),
            ("result_bytes", pa.int64()),
            ("duration_ms", pa.int64()),
            ("reserved_memory_bytes", pa.int64()),
            ("reserved_threads", pa.int32()),
            ("error", pa.string()),
            ("started_at", _TS),
            ("finished_at", _TS),
        ]
    ),
)

ACCESS_AUDIT = SystemTable(
    namespace=ACCESS_NAMESPACE,
    name="audit",
    mode="append",
    schema=pa.schema(
        [
            ("event_time", _TS),
            ("query_id", pa.string()),
            ("actor", pa.string()),
            ("action", pa.string()),
            ("workspace_slug", pa.string()),
            ("status", pa.string()),
        ]
    ),
)

INFO_CATALOGS = SystemTable(
    namespace=INFO_NAMESPACE,
    name="catalogs",
    mode="overwrite",
    schema=pa.schema(
        [
            ("catalog", pa.string()),
            ("polaris_name", pa.string()),
            ("storage_kind", pa.string()),
            ("is_system", pa.bool_()),
            ("created_at", _TS),
        ]
    ),
)

INFO_SCHEMAS = SystemTable(
    namespace=INFO_NAMESPACE,
    name="schemas",
    mode="overwrite",
    schema=pa.schema([("catalog", pa.string()), ("schema_name", pa.string())]),
)

INFO_TABLES = SystemTable(
    namespace=INFO_NAMESPACE,
    name="tables",
    mode="overwrite",
    schema=pa.schema(
        [
            ("catalog", pa.string()),
            ("schema_name", pa.string()),
            ("table_name", pa.string()),
            ("owner_email", pa.string()),
            ("row_count", pa.int64()),
            ("size_bytes", pa.int64()),
            ("last_write_at", _TS),
        ]
    ),
)

INFO_COLUMNS = SystemTable(
    namespace=INFO_NAMESPACE,
    name="columns",
    mode="overwrite",
    schema=pa.schema(
        [
            ("catalog", pa.string()),
            ("schema_name", pa.string()),
            ("table_name", pa.string()),
            ("column_name", pa.string()),
            ("data_type", pa.string()),
            ("ordinal", pa.int32()),
        ]
    ),
)

ALL_TABLES = (
    QUERY_HISTORY,
    ACCESS_AUDIT,
    INFO_CATALOGS,
    INFO_SCHEMAS,
    INFO_TABLES,
    INFO_COLUMNS,
)
