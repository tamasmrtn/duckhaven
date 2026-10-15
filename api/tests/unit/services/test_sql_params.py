"""Typed query parameters (`services/sql_params.py`)."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import duckdb
import pytest

from api.schemas.query import TypedParameter
from api.services.sql_params import InvalidParameters, render


def _run(sql: str, parameters) -> tuple:
    """Render, then run the result in a scratch DuckDB to see what the engine reads."""
    conn = duckdb.connect()
    try:
        return conn.execute(render(sql, parameters)).fetchone()
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("sql", "parameters"),
    [
        ("SELECT ?, ?", [1, "a"]),
        ("SELECT ?1, ?2, ?1", [1, "a"]),
        ("SELECT $1, $2, $1", [1, "a"]),
        ("SELECT $n, $s, $n", {"n": 1, "s": "a"}),
    ],
)
def test_every_placeholder_style(sql, parameters):
    assert _run(sql, parameters)[:2] == (1, "a")


def test_plain_json_values_render_as_their_natural_type():
    row = _run(
        "SELECT ?, ?, ?, ?, ?, ?",
        [None, True, 9007199254740993, 2**70, 0.1, "it's"],
    )
    assert row == (None, True, 9007199254740993, 2**70, 0.1, "it's")


def test_typed_values_render_as_their_type():
    row = _run(
        "SELECT ?, ?, ?, ?, ?, ?, ?",
        [
            TypedParameter(type="DECIMAL(18,4)", value="12.3456"),
            TypedParameter(type="DATE", value="2024-01-02"),
            TypedParameter(type="TIMESTAMPTZ", value="2024-01-02 03:04:05+00"),
            TypedParameter(type="UUID", value="00000000-0000-0000-0000-000000000001"),
            TypedParameter(type="BLOB", value="YWJj"),
            TypedParameter(type="BIGINT[]", value=[1, None, 3]),
            TypedParameter(type="VARCHAR", value=None),
        ],
    )
    assert row[0] == Decimal("12.3456")
    assert row[1] == dt.date(2024, 1, 2)
    assert row[2] == dt.datetime(2024, 1, 2, 3, 4, 5, tzinfo=dt.UTC)
    assert row[3] == uuid.UUID(int=1)
    assert row[4] == b"abc"
    assert row[5] == [1, None, 3]
    assert row[6] is None


def test_a_negative_value_after_a_minus_is_not_a_comment():
    """Unparenthesized, `10-?` with -5 renders `10--5`: the rest becomes a comment."""
    assert render("SELECT 10-?", [-5]) == "SELECT 10-(-5)"
    assert _run("SELECT 10-?, 2", [-5]) == (15, 2)


def test_placeholders_in_strings_comments_and_quoted_names_are_left_alone():
    sql = "SELECT ? AS \"q?\", '?' AS s, $$a ? b$$ AS h -- ?\n, /* $1 */ 1"
    assert render(sql, [7]).startswith("SELECT (7) AS \"q?\", '?' AS s, $$a ? b$$")
    assert _run(sql, [7]) == (7, "?", "a ? b", 1)


def test_a_cast_right_after_a_placeholder():
    assert _run("SELECT ?::DATE", ["2024-01-02"]) == (dt.date(2024, 1, 2),)


def test_quotes_in_values_cannot_escape_the_literal():
    payload = "x'); DROP TABLE t; --"
    assert _run("SELECT ?", [payload]) == (payload,)


@pytest.mark.parametrize(
    "type_text",
    ["DATE); DROP TABLE t; --", "VARCHAR(10) || 'x'", "DECIMAL(99,1)", "TEXT", ""],
)
def test_types_outside_the_grammar_are_refused(type_text):
    with pytest.raises(InvalidParameters, match="Unsupported|Invalid DECIMAL"):
        render("SELECT ?", [TypedParameter(type=type_text, value="1")])


@pytest.mark.parametrize(
    ("sql", "parameters", "message"),
    [
        ("SELECT ?, $1", [1], "one placeholder style"),
        ("SELECT ?, ?2", [1, 2], "one placeholder style"),
        ("SELECT ?, ?", [1], "every parameter must be used"),
        ("SELECT ?", [1, 2], "every parameter must be used"),
        ("SELECT $1", {"1": 1}, "as a list"),
        ("SELECT $a", [1], "as an object"),
        ("SELECT $a", {"a": 1, "b": 2}, "unused: \\['b'\\]"),
        ("SELECT ?; SELECT ?", [1, 2], "exactly one statement"),
        ("SELECT * FROM t ORDER BY ?", [1], "ORDER BY or GROUP BY"),
        ("SELECT count(*) FROM t GROUP BY ?", [1], "ORDER BY or GROUP BY"),
        ("SELECT ?", [float("nan")], "NaN"),
    ],
)
def test_misfits_say_what_to_fix(sql, parameters, message):
    with pytest.raises(InvalidParameters, match=message):
        render(sql, parameters)


def test_without_parameters_the_sql_is_untouched():
    assert render("SELECT ?", None) == "SELECT ?"


def test_a_bound_file_path_is_visible_to_the_checks():
    """The point of rendering: the gates read the path the statement will open."""
    assert "read_parquet(('/etc/passwd'))" in render(
        "SELECT * FROM read_parquet(?)", ["/etc/passwd"]
    )
