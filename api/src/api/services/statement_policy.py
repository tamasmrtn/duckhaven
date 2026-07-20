"""Capability-scoped statement policy for the SQL session path.

This relaxes the hard ``sql_guard`` allowlist (which stays in force on the
single-shot ``/queries`` path) into a per-statement policy that admits the
statements dbt/dlt need — ``COPY`` to/from the session's scoped staging prefix,
a safe subset of ``SET``, ``ATTACH`` of only the managed catalog, and transaction
control — while still rejecting sandbox escapes: local-FS or arbitrary-URL
``COPY``/``read_*``, arbitrary ``INSTALL``/``LOAD``, and ``ATTACH`` of anything
but the managed catalog.

It runs **at the API** (I1/I8 stay enforced at the boundary) using ``sqlglot`` —
the same pure-Python parser ``grants.py`` uses — so no DuckDB connection is
opened. Unparseable or unrecognized SQL is rejected fail-closed.
"""

from __future__ import annotations

import posixpath

import sqlglot
from sqlglot import exp

# SET names dbt/dlt legitimately need. Everything else is denied — crucially
# anything that could widen the sandbox (memory_limit, threads,
# enable_external_access, allowed_directories/allowed_paths, disabled_filesystems,
# home_directory, secret_directory, extension_directory).
_ALLOWED_SET_NAMES = {"timezone", "time zone", "search_path", "schema"}

# Table functions that read a file/URL path; their first positional argument is a
# path we must confine to the staging prefix (closes the read_parquet('s3://…')
# read/exfiltration gap).
_FILE_FUNCTION_NAMES = {
    "read_csv",
    "read_csv_auto",
    "read_json",
    "read_json_auto",
    "read_ndjson",
    "read_ndjson_auto",
    "read_ndjson_objects",
    "read_text",
    "read_blob",
    "read_parquet",
    "parquet_scan",
    "glob",
    # Metadata/probe readers. They return schema rather than data, but they still
    # perform a fetch of an arbitrary path — enough to work as a read oracle and
    # to reach a host the agent should not touch.
    "sniff_csv",
    "parquet_metadata",
    "parquet_schema",
    # Table-format scanners that take a location instead of a catalog relation.
    "iceberg_scan",
    "delta_scan",
}

# Statement node types admitted outright (their file-function args are still
# checked). Data + catalog DDL + session context + transaction control.
_ALLOWED_STATEMENT_NODES = (
    exp.Select,
    exp.Union,
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Alter,
    exp.Drop,
    exp.Use,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    # DESCRIBE is read-only relation/column introspection (dbt uses it for column
    # metadata, contracts, and `dbt show`); it reads no files and mutates nothing.
    exp.Describe,
)


class StatementNotAllowed(ValueError):
    """Raised when a session statement violates the capability-scoped policy.

    Carries a user-facing detail in ``args[0]`` and a short ``rule`` slug (for the
    ``duckhaven_statement_policy_rejections_total`` metric). Routers re-raise as
    HTTP 422 ``{"error": "statement_not_allowed", "detail": ...}``."""

    def __init__(self, message: str, rule: str) -> None:
        super().__init__(message)
        self.rule = rule


def _normalize(path: str) -> str:
    """Collapse ``.``/``..`` segments before a prefix compare.

    Without this, ``'<staging>/../../escaped.parquet'`` passes ``startswith`` and
    then resolves *outside* the prefix — verified against DuckDB, which wrote the
    escaped file. ``normpath`` is applied to the path portion only so a scheme's
    ``//`` (``s3://``, ``https://``) survives.
    """
    scheme, sep, rest = path.partition("://")
    if sep:
        return f"{scheme}{sep}{posixpath.normpath(rest)}"
    return posixpath.normpath(path)


def _is_under_staging(path: str, staging_prefixes: list[str]) -> bool:
    normalized = _normalize(path)
    return any(normalized.startswith(_normalize(prefix)) for prefix in staging_prefixes if prefix)


def _positional_path_arg(node: exp.Expression) -> exp.Expression | None:
    """The first positional argument of a file table function (the path)."""
    if isinstance(node, exp.ReadCSV):
        return node.this
    exprs = node.expressions
    return exprs[0] if exprs else None


def _paths_from_arg(arg: exp.Expression | None) -> list[str] | None:
    """String path(s) from a file-function's path argument, or ``None`` when it is
    not a resolvable string literal (a bind param / expression we cannot verify)."""
    if arg is None:
        return None
    if isinstance(arg, exp.Literal) and arg.is_string:
        return [arg.this]
    if isinstance(arg, exp.Array):
        literals = [e for e in arg.expressions if isinstance(e, exp.Literal) and e.is_string]
        if len(literals) == len(arg.expressions) and literals:
            return [e.this for e in literals]
    return None


def _looks_like_a_path(name: str) -> bool:
    """True when a quoted relation name is really a file/URL for DuckDB's
    replacement scan, rather than an ordinary quoted identifier.

    ``SELECT * FROM 'http://host/x.parquet'`` is a valid DuckDB read, but sqlglot
    models the literal exactly like a double-quoted identifier (``Table`` with a
    ``quoted=True`` Identifier), so the two are indistinguishable by shape alone.
    A URL scheme or a path separator is the discriminator: dbt quotes identifiers
    containing spaces, case, or reserved words — never ``://`` or ``/``.
    """
    return "://" in name or "/" in name


def _check_replacement_scans(stmt: exp.Expression, staging_prefixes: list[str]) -> None:
    """Confine DuckDB replacement scans (``FROM '<path or URL>'``) to staging."""
    for table in stmt.find_all(exp.Table):
        ident = table.this
        if not (isinstance(ident, exp.Identifier) and ident.quoted):
            continue
        name = ident.name
        if not _looks_like_a_path(name):
            continue
        if not _is_under_staging(name, staging_prefixes):
            raise StatementNotAllowed(
                f"Reading directly from a path may only target the staging prefix, not {name!r}",
                "read_path",
            )


def _check_file_functions(stmt: exp.Expression, staging_prefixes: list[str]) -> None:
    for node in stmt.walk():
        name: str | None = None
        if isinstance(node, exp.ReadParquet | exp.ReadCSV):
            name = "read_parquet" if isinstance(node, exp.ReadParquet) else "read_csv"
        elif isinstance(node, exp.Anonymous) and isinstance(node.this, str):
            candidate = node.this.lower()
            if candidate in _FILE_FUNCTION_NAMES:
                name = candidate
        if name is None:
            continue
        paths = _paths_from_arg(_positional_path_arg(node))
        if paths is None:
            raise StatementNotAllowed(
                f"{name}() path must be a string literal under the staging prefix",
                "read_path",
            )
        for path in paths:
            if not _is_under_staging(path, staging_prefixes):
                raise StatementNotAllowed(
                    f"{name}() may only read from the staging prefix, not {path!r}",
                    "read_path",
                )


def _check_set(stmt: exp.Set) -> None:
    for item in stmt.expressions:
        column = item.find(exp.Column)
        name = column.name.lower() if column is not None else None
        if name is None or name not in _ALLOWED_SET_NAMES:
            raise StatementNotAllowed(
                f"SET {name or '?'} is not permitted (allowed: "
                f"{', '.join(sorted(_ALLOWED_SET_NAMES))})",
                "set_name",
            )


def _check_copy(stmt: exp.Copy, staging_prefixes: list[str]) -> None:
    files = stmt.args.get("files") or []
    if not files:
        raise StatementNotAllowed("COPY without a resolvable file target", "copy_path")
    for f in files:
        if not (isinstance(f, exp.Literal) and f.is_string):
            raise StatementNotAllowed(
                "COPY target must be a string literal under the staging prefix",
                "copy_path",
            )
        if not _is_under_staging(f.this, staging_prefixes):
            raise StatementNotAllowed(
                f"COPY may only read/write the staging prefix, not {f.this!r}",
                "copy_path",
            )


def _check_attach(stmt: exp.Attach, managed_catalogs: set[str]) -> None:
    literal = stmt.find(exp.Literal)
    name = literal.this if literal is not None and literal.is_string else None
    if name is None or name not in managed_catalogs:
        raise StatementNotAllowed(
            f"ATTACH is only permitted for the managed catalog(s), not {name!r}",
            "attach_target",
        )


def _check_statement(
    stmt: exp.Expression, staging_prefixes: list[str], managed_catalogs: set[str]
) -> None:
    # File-reading functions and bare-path replacement scans are checked in every
    # statement type (a SELECT/INSERT can embed read_parquet('s3://…') or
    # FROM 'http://…').
    _check_file_functions(stmt, staging_prefixes)
    _check_replacement_scans(stmt, staging_prefixes)

    if isinstance(stmt, exp.Set):
        _check_set(stmt)
    elif isinstance(stmt, exp.Copy):
        _check_copy(stmt, staging_prefixes)
    elif isinstance(stmt, exp.Attach):
        _check_attach(stmt, managed_catalogs)
    elif isinstance(stmt, exp.Install):
        raise StatementNotAllowed("INSTALL is not permitted", "install")
    elif isinstance(stmt, exp.Command):
        # LOAD and any statement sqlglot could only parse as a raw Command
        # (unsupported/unknown syntax) are rejected fail-closed.
        keyword = str(stmt.this).upper()
        raise StatementNotAllowed(f"{keyword} is not permitted", "command")
    elif not isinstance(stmt, _ALLOWED_STATEMENT_NODES):
        raise StatementNotAllowed(
            f"Statement type {type(stmt).__name__} is not permitted", "unknown"
        )


def assert_statement_allowed(
    sql: str, *, staging_prefixes: list[str], managed_catalogs: set[str]
) -> None:
    """Raise ``StatementNotAllowed`` if any statement in ``sql`` violates policy.

    ``staging_prefixes`` are the object-storage URI prefixes a ``COPY``/``read_*``
    may touch (the session's ``staging_uri`` + its catalog roots); ``managed_catalogs``
    are the catalog names an ``ATTACH`` may reference.
    """
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception as exc:  # noqa: BLE001 - any parse failure is fail-closed
        raise StatementNotAllowed(f"SQL parse error: {exc}", "unparseable") from exc

    parsed = [s for s in statements if s is not None]
    if not parsed:
        raise StatementNotAllowed("Empty SQL", "empty")

    for stmt in parsed:
        _check_statement(stmt, staging_prefixes, managed_catalogs)
