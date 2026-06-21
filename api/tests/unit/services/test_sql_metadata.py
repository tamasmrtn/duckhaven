"""Unit tests for the SQL-metadata row transforms (no agent required)."""

from __future__ import annotations

from api.services import sql_metadata


def test_build_signature_pairs_names_and_types():
    sig = sql_metadata.build_signature("concat", "a, b", "VARCHAR, VARCHAR", None, "VARCHAR")
    assert sig == "concat(a VARCHAR, b VARCHAR) → VARCHAR"


def test_build_signature_names_only():
    sig = sql_metadata.build_signature("f", "x, y", "", None, "INTEGER")
    assert sig == "f(x, y) → INTEGER"


def test_build_signature_types_only_with_varargs():
    sig = sql_metadata.build_signature("greatest", "", "INTEGER", "INTEGER", "INTEGER")
    assert sig == "greatest(INTEGER, INTEGER...) → INTEGER"


def test_build_signature_no_params_no_return():
    assert sql_metadata.build_signature("now", "", "", None, None) == "now()"


def test_functions_from_rows_maps_and_nulls_examples():
    rows = [
        {
            "function_name": "abs",
            "function_type": "scalar",
            "return_type": "BIGINT",
            "parameters": "x",
            "parameter_types": "BIGINT",
            "varargs": None,
            "examples": "abs(-3)",
        },
        {
            "function_name": "count",
            "function_type": "aggregate",
            "return_type": "BIGINT",
            "parameters": "",
            "parameter_types": "ANY",
            "varargs": None,
            "examples": None,
        },
    ]
    out = sql_metadata.functions_from_rows(rows)
    assert [f.name for f in out] == ["abs", "count"]
    assert out[0].signature == "abs(x BIGINT) → BIGINT"
    assert out[0].examples == "abs(-3)"
    assert out[1].type == "aggregate"
    assert out[1].examples is None


def test_keywords_from_rows():
    out = sql_metadata.keywords_from_rows(
        [{"keyword_name": "select", "keyword_category": "reserved"}]
    )
    assert out[0].name == "select"
    assert out[0].category == "reserved"


def test_types_from_rows():
    out = sql_metadata.types_from_rows([{"type_name": "INTEGER", "type_category": "NUMERIC"}])
    assert out[0].name == "INTEGER"
    assert out[0].category == "NUMERIC"
