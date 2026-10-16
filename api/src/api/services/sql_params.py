"""Typed query parameters, rendered into the SQL before anything checks it.

A client sends SQL with placeholders (``?``, ``?1``, ``$1`` or ``$name``) and the
values separately, as JSON. The API renders each value as a typed DuckDB literal in
place of its placeholder, and from then on the statement is ordinary literal SQL:
the SQL gate, the grant check, the result cache key, lineage and the agent all see
exactly the statement that runs. Binding the values on the agent instead would let
a bound path (``read_parquet(?)``) or function argument slip past checks that read
the SQL text.

Every literal is parenthesized. Without that, ``10-?`` with ``-5`` would render as
``10--5``, and ``--`` starts a comment.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

import duckdb
import sqlglot
from fastapi import HTTPException, status
from sqlglot import exp

from api.schemas.query import TypedParameter


class InvalidParameters(ValueError):
    """The parameters do not fit the SQL. ``args[0]`` says what to fix."""


_SCALAR_TYPES = (
    "BOOLEAN",
    "TINYINT",
    "SMALLINT",
    "INTEGER",
    "BIGINT",
    "HUGEINT",
    "UTINYINT",
    "USMALLINT",
    "UINTEGER",
    "UBIGINT",
    "UHUGEINT",
    "FLOAT",
    "DOUBLE",
    "VARCHAR",
    "BLOB",
    "DATE",
    "TIME",
    "TIMESTAMP",
    "TIMESTAMPTZ",
    "TIMESTAMP WITH TIME ZONE",
    "INTERVAL",
    "UUID",
    "JSON",
)
# The only type spellings a parameter may name. The type is spliced into the SQL,
# so it is matched against this grammar, never passed through.
_TYPE = re.compile(
    r"^(?P<base>"
    + "|".join(sorted((re.escape(t) for t in _SCALAR_TYPES), key=len, reverse=True))
    + r"|DECIMAL\((?P<precision>\d{1,2}),\s*(?P<scale>\d{1,2})\))(?P<dims>(\[\])*)$",
    re.IGNORECASE,
)

_INT64 = range(-(2**63), 2**63)


@dataclass(frozen=True)
class _Placeholder:
    start: int  # first character to replace
    end: int  # one past the last character to replace
    kind: str  # "?", "?N", "$N" or "$name"
    key: str  # the name DuckDB gives it: "1", "2", … or the bare name


def render(sql: str, parameters: list[Any] | dict[str, Any] | None) -> str:
    """``sql`` with every placeholder replaced by its value as a typed literal.

    Without ``parameters`` the SQL is returned untouched. Raises
    :class:`InvalidParameters` when they do not fit.
    """
    if parameters is None:
        return sql
    try:
        statements = duckdb.extract_statements(sql)
    except duckdb.Error as exc:
        raise InvalidParameters(f"Could not parse the SQL to place parameters: {exc}") from exc
    if len(statements) != 1:
        raise InvalidParameters("Parameters can only be used with exactly one statement.")
    placeholders = _placeholders(sql)
    if {p.key for p in placeholders} != set(statements[0].named_parameters):
        raise InvalidParameters("Could not locate every placeholder in the SQL.")
    values = _assign(placeholders, parameters)
    _refuse_positional_clauses(sql, placeholders)

    out = sql
    for placeholder in sorted(placeholders, key=lambda p: p.start, reverse=True):
        literal = _literal(values[placeholder.key])
        out = out[: placeholder.start] + literal + out[placeholder.end :]
    return out


def _placeholders(sql: str) -> list[_Placeholder]:
    """Every placeholder outside strings, comments and quoted identifiers."""
    try:
        tokens = sqlglot.tokenize(sql, read="duckdb")
    except Exception as exc:  # noqa: BLE001 - sqlglot raises several error types
        raise InvalidParameters(f"Could not read the SQL to place parameters: {exc}") from exc
    found: list[_Placeholder] = []
    positional = 0
    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        adjacent = nxt is not None and nxt.start == tok.end + 1
        kind = tok.token_type.name
        if kind == "PLACEHOLDER" and adjacent and nxt.token_type.name == "NUMBER":
            found.append(_Placeholder(tok.start, nxt.end + 1, "?N", nxt.text))
        elif kind in ("PLACEHOLDER", "QDCOLON"):
            # `?::DATE` arrives as one token; only its `?` is the placeholder.
            positional += 1
            found.append(_Placeholder(tok.start, tok.start + 1, "?", str(positional)))
        elif kind == "PARAMETER" and adjacent and re.fullmatch(r"\w+", nxt.text):
            numbered = nxt.text.isdigit()
            found.append(
                _Placeholder(tok.start, nxt.end + 1, "$N" if numbered else "$name", nxt.text)
            )
    return found


def _assign(placeholders: list[_Placeholder], parameters: list[Any] | dict[str, Any]) -> dict:
    """Each placeholder key's value, checking the parameters fit the placeholders."""
    kinds = {p.kind for p in placeholders}
    if len(kinds) > 1:
        raise InvalidParameters(
            "Use one placeholder style per statement: ?, ?1, $1 or $name, not a mix."
        )
    keys = {p.key for p in placeholders}
    if kinds == {"$name"}:
        if not isinstance(parameters, dict):
            raise InvalidParameters("Named placeholders ($name) take parameters as an object.")
        if set(parameters) != keys:
            missing = sorted(keys - set(parameters))
            unused = sorted(set(parameters) - keys)
            raise InvalidParameters(
                f"Parameters do not match the placeholders (missing: {missing}, unused: {unused})."
            )
        return dict(parameters)
    if not isinstance(parameters, list):
        raise InvalidParameters("Positional placeholders (?, ?1, $1) take parameters as a list.")
    expected = {str(n) for n in range(1, len(parameters) + 1)}
    if keys != expected:
        raise InvalidParameters(
            f"The statement has {len(keys)} placeholder(s) but {len(parameters)} "
            "parameter(s) were given; every parameter must be used."
        )
    return {str(n): v for n, v in enumerate(parameters, start=1)}


def _refuse_positional_clauses(sql: str, placeholders: list[_Placeholder]) -> None:
    """DuckDB rejects a parameter in ORDER BY or GROUP BY, and a literal there means
    "column number N" instead: refuse rather than change the query's meaning.

    Checked on a copy with each placeholder swapped for a marker name. When sqlglot
    cannot parse that copy the check is skipped: it guards against a silent change
    of meaning, not a security boundary, which the gates after rendering hold.
    """
    marked = sql
    for i, p in sorted(enumerate(placeholders), key=lambda e: e[1].start, reverse=True):
        marked = marked[: p.start] + f"__dh_param_{i}__" + marked[p.end :]
    try:
        trees = sqlglot.parse(marked, read="duckdb")
    except Exception:  # noqa: BLE001
        return
    for tree in trees:
        if tree is None:
            continue
        for clause in (*tree.find_all(exp.Order), *tree.find_all(exp.Group)):
            for column in clause.find_all(exp.Column):
                if column.name.startswith("__dh_param_"):
                    raise InvalidParameters(
                        "Placeholders are not allowed in ORDER BY or GROUP BY: a value "
                        "there would be read as a column position."
                    )


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _literal(value: Any) -> str:
    if isinstance(value, TypedParameter):
        return "(" + _typed(value.type, value.value) + ")"
    return "(" + _plain(value) + ")"


def _plain(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value) if value in _INT64 else f"CAST({_quote(str(value))} AS HUGEINT)"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InvalidParameters("A parameter cannot be NaN or infinite.")
        return f"CAST({_quote(repr(value))} AS DOUBLE)"
    if isinstance(value, str):
        return _quote(value)
    raise InvalidParameters(
        f"Unsupported parameter value {value!r}: send a string, number, boolean or null, "
        'or {"type": ..., "value": ...}.'
    )


def _typed(type_text: Any, value: Any) -> str:
    if not isinstance(type_text, str) or (match := _TYPE.match(type_text.strip())) is None:
        raise InvalidParameters(f"Unsupported parameter type {type_text!r}.")
    if match["precision"] is not None:
        precision, scale = int(match["precision"]), int(match["scale"])
        if not 1 <= precision <= 38 or scale > precision:
            raise InvalidParameters(f"Invalid DECIMAL precision or scale in {type_text!r}.")
    base = match["base"].upper()
    dims = len(match["dims"]) // 2
    canonical = re.sub(r"\s+", " ", base) + "[]" * dims
    if value is None:
        return f"CAST(NULL AS {canonical})"
    return f"CAST({_typed_value(base, dims, value)} AS {canonical})"


def _typed_value(base: str, dims: int, value: Any) -> str:
    if dims:
        if not isinstance(value, list):
            raise InvalidParameters("A list type takes a JSON array as its value.")
        element = base + "[]" * (dims - 1)
        return (
            "["
            + ", ".join(
                "NULL" if v is None else f"CAST({_typed_value(base, dims - 1, v)} AS {element})"
                for v in value
            )
            + "]"
        )
    if base == "BLOB":
        if not isinstance(value, str):
            raise InvalidParameters("A BLOB parameter takes its bytes base64-encoded.")
        return f"from_base64({_quote(value)})"
    if isinstance(value, bool):
        return _quote("true" if value else "false")
    if isinstance(value, int | float | str):
        return _quote(str(value) if not isinstance(value, float) else repr(value))
    raise InvalidParameters(f"Unsupported value {value!r} for type {base}.")


def render_for_request(sql: str, parameters: list[Any] | dict[str, Any] | None) -> str:
    """:func:`render`, with a misfit reported as the route's 422."""
    try:
        return render(sql, parameters)
    except InvalidParameters as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_parameters", "detail": str(exc)},
        ) from exc
