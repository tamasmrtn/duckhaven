"""DuckDB's names for catalog column types, as measured against DESCRIBE on 1.5.6."""

from __future__ import annotations

import pytest

from api.services.catalog_types import ducklake_type, field_name, iceberg_type


@pytest.mark.parametrize(
    ("iceberg", "duckdb"),
    [
        ("int", "INTEGER"),
        ("long", "BIGINT"),
        ("float", "FLOAT"),
        ("double", "DOUBLE"),
        ("decimal(10, 2)", "DECIMAL(10,2)"),
        ("string", "VARCHAR"),
        ("boolean", "BOOLEAN"),
        ("date", "DATE"),
        ("time", "TIME"),
        ("timestamp", "TIMESTAMP"),
        ("timestamptz", "TIMESTAMP WITH TIME ZONE"),
        ("uuid", "UUID"),
        ("binary", "BLOB"),
        # Not measured: kept as the catalog spells it.
        ("fixed[16]", "FIXED[16]"),
    ],
)
def test_an_iceberg_primitive(iceberg, duckdb):
    assert iceberg_type(iceberg) == duckdb


def test_iceberg_nested_types():
    struct = {
        "type": "struct",
        "fields": [
            {"id": 18, "name": "x", "required": False, "type": "int"},
            {"id": 19, "name": "y z", "required": False, "type": "string"},
        ],
    }
    assert iceberg_type(struct) == 'STRUCT(x INTEGER, "y z" VARCHAR)'
    assert iceberg_type({"type": "list", "element-id": 20, "element": "int"}) == "INTEGER[]"
    assert (
        iceberg_type({"type": "map", "key-id": 21, "key": "string", "value": "long"})
        == "MAP(VARCHAR, BIGINT)"
    )
    assert iceberg_type({"type": "list", "element": struct}) == f"{iceberg_type(struct)}[]"


@pytest.mark.parametrize(
    ("ducklake", "duckdb"),
    [
        ("int8", "TINYINT"),
        ("int16", "SMALLINT"),
        ("int32", "INTEGER"),
        ("int64", "BIGINT"),
        ("int128", "HUGEINT"),
        ("uint8", "UTINYINT"),
        ("uint64", "UBIGINT"),
        ("uint128", "UHUGEINT"),
        ("float32", "FLOAT"),
        ("float64", "DOUBLE"),
        ("decimal(38,6)", "DECIMAL(38,6)"),
        ("varchar", "VARCHAR"),
        ("timetz", "TIME WITH TIME ZONE"),
        ("timestamptz", "TIMESTAMP WITH TIME ZONE"),
        ("timestamp_ms", "TIMESTAMP_MS"),
        ("interval", "INTERVAL"),
        ("json", "JSON"),
        ("blob", "BLOB"),
    ],
)
def test_a_ducklake_primitive(ducklake, duckdb):
    assert ducklake_type(ducklake) == duckdb


def test_ducklake_nested_types_are_built_from_their_children():
    struct = ducklake_type("struct", [("x", "INTEGER"), ("y z", "VARCHAR")])
    assert struct == 'STRUCT(x INTEGER, "y z" VARCHAR)'
    assert ducklake_type("list", [("element", "INTEGER")]) == "INTEGER[]"
    assert ducklake_type("map", [("key", "VARCHAR"), ("value", "BIGINT")]) == "MAP(VARCHAR, BIGINT)"


def test_a_field_name_is_quoted_as_duckdb_quotes_it():
    """Measured: DuckDB quotes every keyword, reserved or not."""
    assert [field_name(n) for n in ("x", "_a1", "order", "value", "year", "Name", "1a")] == [
        "x",
        "_a1",
        '"order"',
        '"value"',
        '"year"',
        '"Name"',
        '"1a"',
    ]
    assert field_name('a"b') == '"a""b"'
