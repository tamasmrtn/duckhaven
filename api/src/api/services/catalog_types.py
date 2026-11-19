"""DuckDB's name for a catalog's column type, as ``DESCRIBE`` shows it to a query.

Each catalog stores a type in its own spelling: Iceberg's ``long``, DuckLake's
``int64``. The names below were measured against DuckDB 1.5.6's ``DESCRIBE`` on
tables made with each type through the ``iceberg`` and ``ducklake`` extensions.
A type they do not cover keeps the catalog's spelling, upper-cased.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Sequence
from typing import Any

import duckdb

_ICEBERG: dict[str, str] = {
    "boolean": "BOOLEAN",
    "int": "INTEGER",
    "long": "BIGINT",
    "float": "FLOAT",
    "double": "DOUBLE",
    "date": "DATE",
    "time": "TIME",
    "timestamp": "TIMESTAMP",
    "timestamptz": "TIMESTAMP WITH TIME ZONE",
    "string": "VARCHAR",
    "uuid": "UUID",
    "binary": "BLOB",
}

_DUCKLAKE: dict[str, str] = {
    "int8": "TINYINT",
    "int16": "SMALLINT",
    "int32": "INTEGER",
    "int64": "BIGINT",
    "int128": "HUGEINT",
    "uint8": "UTINYINT",
    "uint16": "USMALLINT",
    "uint32": "UINTEGER",
    "uint64": "UBIGINT",
    "uint128": "UHUGEINT",
    "float32": "FLOAT",
    "float64": "DOUBLE",
    "varchar": "VARCHAR",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "time": "TIME",
    "timetz": "TIME WITH TIME ZONE",
    "timestamp": "TIMESTAMP",
    "timestamptz": "TIMESTAMP WITH TIME ZONE",
    "timestamp_s": "TIMESTAMP_S",
    "timestamp_ms": "TIMESTAMP_MS",
    "timestamp_ns": "TIMESTAMP_NS",
    "interval": "INTERVAL",
    "uuid": "UUID",
    "blob": "BLOB",
    "json": "JSON",
}

# Iceberg writes `decimal(10, 2)`, DuckLake `decimal(10,2)`; DuckDB shows `DECIMAL(10,2)`.
_DECIMAL = re.compile(r"decimal\(\s*(\d+)\s*,\s*(\d+)\s*\)")
_BARE = re.compile(r"[a-z_][a-z0-9_]*")


@functools.cache
def _keywords() -> frozenset[str]:
    with duckdb.connect() as conn:
        rows = conn.execute("SELECT keyword_name FROM duckdb_keywords()").fetchall()
    return frozenset(row[0] for row in rows)


def field_name(name: str) -> str:
    """A struct field's name as DuckDB writes it in a type: bare when it can be,
    which excludes every keyword, not only the reserved ones."""
    if _BARE.fullmatch(name) and name not in _keywords():
        return name
    return '"' + name.replace('"', '""') + '"'


def _decimal(text: str) -> str | None:
    match = _DECIMAL.fullmatch(text)
    return f"DECIMAL({match[1]},{match[2]})" if match else None


def iceberg_type(type_: Any) -> str:
    """DuckDB's name for an Iceberg schema field's ``type``: a primitive's string,
    or a nested type's object."""
    if isinstance(type_, dict):
        kind = type_.get("type")
        if kind == "struct":
            fields = ", ".join(
                f"{field_name(f['name'])} {iceberg_type(f['type'])}"
                for f in type_.get("fields") or []
            )
            return f"STRUCT({fields})"
        if kind == "list":
            return f"{iceberg_type(type_.get('element'))}[]"
        if kind == "map":
            return f"MAP({iceberg_type(type_.get('key'))}, {iceberg_type(type_.get('value'))})"
        return str(kind).upper()
    text = str(type_).strip().lower()
    return _decimal(text) or _ICEBERG.get(text, text.upper())


def ducklake_type(type_: str, children: Sequence[tuple[str, str]] = ()) -> str:
    """DuckDB's name for a DuckLake column's ``column_type``.

    ``children`` are a nested column's child columns, in order, as ``(name,
    DuckDB type)``: a struct's fields, a list's ``element``, a map's ``key`` and
    ``value``.
    """
    text = type_.strip().lower()
    if text == "struct":
        return "STRUCT(" + ", ".join(f"{field_name(n)} {t}" for n, t in children) + ")"
    if text == "list" and len(children) == 1:
        return f"{children[0][1]}[]"
    if text == "map" and len(children) == 2:
        return f"MAP({children[0][1]}, {children[1][1]})"
    return _decimal(text) or _DUCKLAKE.get(text, text.upper())
