"""Capability-scoped statement policy for the SQL session path.

This relaxes the hard ``sql_guard`` allowlist (which stays in force on the
single-shot ``/queries`` path) into a per-statement policy that admits the
statements dbt/dlt need — ``COPY`` to/from the session's scoped staging prefix,
a safe subset of ``SET``, ``ATTACH`` of only the managed catalog, transaction
control, and the read-only introspection statements (``DESCRIBE``, ``SHOW``,
``SUMMARIZE``, the row-returning ``PRAGMA``s) clients use for relation metadata —
while still rejecting sandbox escapes: local-FS or arbitrary-URL
``COPY``/``read_*``, arbitrary ``INSTALL``/``LOAD``, ``ATTACH`` of anything
but the managed catalog, the configuration-setting form of ``PRAGMA``, and
(via ``sql_denylist``) foreign-database functions and DuckLake's internal
catalog metadata.

It runs **at the API** (I1/I8 stay enforced at the boundary) using ``sqlglot`` —
the same pure-Python parser ``grants.py`` uses — so no DuckDB connection is
opened. Unparseable or unrecognized SQL is rejected fail-closed.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator

import sqlglot
from sqlglot import exp

from api.services.sql_denylist import ForeignAccessDenied, function_name
from api.services.sql_denylist import check_statement as check_denylist

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
    "read_json_objects",
    "read_json_objects_auto",
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
    "parquet_file_metadata",
    "parquet_full_metadata",
    "parquet_kv_metadata",
    "parquet_bloom_probe",
    # Readers from other extensions the agent loads (avro, postgres), and a
    # DuckDB database file read as a table.
    "read_avro",
    "read_postgres_binary",
    "read_duckdb",
    # Table-format scanners that take a location instead of a catalog relation.
    "iceberg_scan",
    "delta_scan",
}

# Suffixes DuckDB's replacement scan reads as a file, so a relation name ending in
# one is a path whatever its quoting.
_FILE_LIKE_EXTENSIONS = frozenset(
    {
        "parquet",
        "csv",
        "tsv",
        "json",
        "jsonl",
        "ndjson",
        "avro",
        "arrow",
        "db",
        "duckdb",
        "txt",
        "blob",
        "xlsx",
        "gz",
        "zst",
    }
)

# What the single-shot query path may read by path: only what the agent reaches
# over the network (docs/reference/sql-support.md). Anything else names the
# agent's own disk.
_ALLOWED_URL_SCHEMES = frozenset(
    {"s3", "s3a", "s3n", "gs", "gcs", "r2", "http", "https", "az", "azure", "abfs", "abfss", "hf"}
)

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
    # It is *the* supported column-metadata path for attached Iceberg catalogs —
    # `information_schema.columns` cannot introspect them (see
    # docs/reference/sql-support.md).
    exp.Describe,
    # SHOW TABLES / SHOW ALL TABLES. Only these two argument-less forms parse as
    # `exp.Show`; `SHOW <relation>` and `SHOW DATABASES` fall back to `exp.Command`
    # and stay rejected by the branch below, so no extra guard is needed here.
    exp.Show,
    # SUMMARIZE is a read: it scans the relation to compute min/max/approx_unique.
    # Admitted like a SELECT, and grant-checked like one (`reader`, not `metadata`).
    exp.Summarize,
    # TRUNCATE is DuckDB's spelling of `DELETE FROM` without a `WHERE` — its
    # grammar builds the very same DeleteStatement — so it is admitted on the
    # same footing as DELETE, which it already shares a plan with. dbt's seed
    # reset emits it. Its target is grant-checked as a write.
    exp.TruncateTable,
)

# The row-returning PRAGMAs, allowlisted by name. DuckDB types these as queries
# (`StatementType.SELECT`) and the agent materializes them like a SELECT, but a
# PRAGMA is also DuckDB's spelling of `SET` — `PRAGMA memory_limit = '10GB'`
# parses as the same `exp.Pragma` node — so the gate has to be the *name*, not the
# shape. Naming them also rejects valueless state toggles (`PRAGMA
# disable_verification`), which an `EQ`-shape check would let through.
_ALLOWED_PRAGMA_NAMES = {"version", "table_info", "database_list", "show_tables"}


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


def _ends_with_file_extension(name: str) -> bool:
    base, _, ext = name.rpartition(".")
    return bool(base) and ext.lower() in _FILE_LIKE_EXTENSIONS


def _looks_like_a_path(name: str) -> bool:
    """True when a quoted relation name is really a file/URL for DuckDB's
    replacement scan, rather than an ordinary quoted identifier.

    ``SELECT * FROM 'http://host/x.parquet'`` is a valid DuckDB read, but sqlglot
    models the literal exactly like a double-quoted identifier (``Table`` with a
    ``quoted=True`` Identifier), so the two are indistinguishable by shape alone.
    A URL scheme, a path separator, a leading dot or a file-extension suffix is the
    discriminator: dbt quotes identifiers containing spaces, case, or reserved
    words — never those — while DuckDB resolves a relative file name too.
    """
    return (
        "://" in name
        or "/" in name
        or "\\" in name
        or name.startswith(".")
        or _ends_with_file_extension(name)
    )


def _has_allowed_scheme(path: str) -> bool:
    scheme, sep, _ = path.partition("://")
    return bool(sep) and scheme.lower() in _ALLOWED_URL_SCHEMES


def _table_path(table: exp.Table) -> str | None:
    """The file a relation names for DuckDB's replacement scan, else ``None``."""
    ident = table.this
    if not isinstance(ident, exp.Identifier):
        return None
    if ident.quoted:
        return ident.name if _looks_like_a_path(ident.name) else None
    parts = [part for part in (table.catalog, table.db, table.name) if part]
    if len(parts) > 1 and table.name.lower() in _FILE_LIKE_EXTENSIONS:
        return ".".join(parts)
    return None


def _write_targets(stmt: exp.Expression) -> list[exp.Expression]:
    """The tables a write or DDL statement names as its target.

    DuckDB resolves a target as a table, never as a file, so a target named
    ``events.json`` is not a read; the statement's source query still is.
    """
    if isinstance(stmt, exp.Drop):
        return list(stmt.args.get("tables") or [])
    if isinstance(stmt, exp.Create | exp.Insert | exp.Alter | exp.Update | exp.Delete | exp.Merge):
        target = stmt.this
        return [target.this if isinstance(target, exp.Schema) else target]
    return []


def _file_read_sites(stmt: exp.Expression) -> Iterator[tuple[str, str | None]]:
    """Every place ``stmt`` reads a file or URL: ``(reader, path)``.

    ``reader`` names the spelling for messages (``read_parquet()``, ``FROM`` for a
    replacement scan, ``SUMMARIZE``); ``path`` is ``None`` when the argument is
    not a string literal and so cannot be checked. One walk, so the session gate,
    the scoped-catalog grant check and the query-path gate see the same reads.
    """
    targets = {id(target) for target in _write_targets(stmt)}
    for node in stmt.walk():
        name: str | None = None
        if isinstance(node, exp.ReadParquet | exp.ReadCSV):
            name = "read_parquet" if isinstance(node, exp.ReadParquet) else "read_csv"
        elif function_name(node) in _FILE_FUNCTION_NAMES:
            name = function_name(node)
        if name is not None:
            paths = _paths_from_arg(_positional_path_arg(node))
            if paths is None:
                yield f"{name}()", None
            else:
                for path in paths:
                    yield f"{name}()", path
        elif isinstance(node, exp.Table):
            if id(node) not in targets and (path := _table_path(node)) is not None:
                yield "FROM", path
        elif (
            # sqlglot parses `SUMMARIZE '<file>'` as a string Literal, not a Table.
            isinstance(node, exp.Summarize)
            and isinstance(node.this, exp.Literal)
            and node.this.is_string
        ):
            yield "SUMMARIZE", node.this.this


def check_file_reads(stmt: exp.Expression, staging_prefixes: list[str]) -> None:
    """Raise ``StatementNotAllowed`` if ``stmt`` reads a file or URL outside staging.

    Shared with the grant check, which applies it with no prefixes at all in a
    workspace holding a scoped catalog.
    """
    for reader, path in _file_read_sites(stmt):
        if path is None:
            raise StatementNotAllowed(
                f"{reader} path must be a string literal under the staging prefix",
                "read_path",
            )
        if not _is_under_staging(path, staging_prefixes):
            raise StatementNotAllowed(_staging_detail(reader, path), "read_path")


def _staging_detail(reader: str, path: str) -> str:
    if reader == "FROM":
        return f"Reading directly from a path may only target the staging prefix, not {path!r}"
    return f"{reader} may only read from the staging prefix, not {path!r}"


def _query_path_denial(path: str) -> StatementNotAllowed:
    return StatementNotAllowed(
        f"Reading {path!r} is not permitted: a query reads files by path only over a URL "
        "(s3://, https://, abfss://, ...), never from the agent's own disk. A SQL session "
        "may also read its staging prefix.",
        "read_path",
    )


def check_query_path_reads(stmt: exp.Expression) -> None:
    """Raise ``StatementNotAllowed`` if ``stmt`` reads anything but a network URL.

    The single-shot query path's file-read rule: documented as limited to what the
    agent's network can reach, so a filesystem path in any spelling is refused in
    every workspace. A statement sqlglot could only keep as a raw ``Command`` is
    checked lexically, since its reads are invisible to the walk.
    """
    if isinstance(stmt, exp.Command):
        check_query_path_reads_lexically(stmt.sql(dialect="duckdb"))
        return
    for reader, path in _file_read_sites(stmt):
        if path is None:
            raise StatementNotAllowed(
                f"{reader} path must be a string literal URL (s3://, https://, ...)",
                "read_path",
            )
        if not _has_allowed_scheme(path):
            raise _query_path_denial(path)


_QUOTED_RELATION = re.compile(
    r"""\b(?:from|join|summarize|describe)\s+(['"])(.*?)\1""", re.IGNORECASE | re.DOTALL
)
_DOTTED_RELATION = re.compile(r"\b(?:from|join)\s+([\w.]+)", re.I)


def check_query_path_reads_lexically(sql: str) -> None:
    """The query-path file-read rule for SQL sqlglot cannot model.

    Over-matches on purpose, like ``sql_denylist``'s lexical fallback: any file
    reader call is refused, because its argument cannot be verified.
    """
    lowered = sql.lower()
    for name in sorted(_FILE_FUNCTION_NAMES):
        if re.search(rf'\b{name}\b"?\s*\(', lowered):
            raise StatementNotAllowed(
                f"{name}() cannot be checked in a statement that does not parse here; "
                "rewrite it so its path can be verified",
                "read_path",
            )
    for match in _QUOTED_RELATION.finditer(sql):
        path = match.group(2)
        if _looks_like_a_path(path) and not _has_allowed_scheme(path):
            raise _query_path_denial(path)
    for match in _DOTTED_RELATION.finditer(sql):
        parts = match.group(1).split(".")
        if len(parts) > 1 and parts[-1].lower() in _FILE_LIKE_EXTENSIONS:
            raise _query_path_denial(match.group(1))


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


def _pragma_name(stmt: exp.Pragma) -> str | None:
    """The pragma's name, across its three parsed shapes: a call
    (``PRAGMA table_info('t')`` -> ``Anonymous``), a bare name (``PRAGMA version``
    -> ``Column``), and the setting form (``PRAGMA x = y`` -> ``EQ`` over a
    ``Column``). ``None`` when the shape is unrecognized, which rejects."""
    node = stmt.this
    if isinstance(node, exp.EQ):
        node = node.this
    if isinstance(node, exp.Anonymous):
        return function_name(node)
    if isinstance(node, exp.Column):
        return node.name.lower()
    return None


def _check_pragma(stmt: exp.Pragma) -> None:
    name = _pragma_name(stmt)
    if name not in _ALLOWED_PRAGMA_NAMES:
        raise StatementNotAllowed(
            f"PRAGMA {name or '?'} is not permitted (allowed: "
            f"{', '.join(sorted(_ALLOWED_PRAGMA_NAMES))})",
            "pragma_name",
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
    check_file_reads(stmt, staging_prefixes)
    # Shared with sql_guard so the gates cannot drift.
    try:
        check_denylist(stmt)
    except ForeignAccessDenied as exc:
        raise StatementNotAllowed(str(exc), exc.rule) from exc

    if isinstance(stmt, exp.Set):
        _check_set(stmt)
    elif isinstance(stmt, exp.Copy):
        _check_copy(stmt, staging_prefixes)
    elif isinstance(stmt, exp.Attach):
        _check_attach(stmt, managed_catalogs)
    elif isinstance(stmt, exp.Pragma):
        _check_pragma(stmt)
    elif isinstance(stmt, exp.TruncateTable) and stmt.args.get("is_database"):
        # sqlglot reads `TRUNCATE DATABASE d` as a database-wide form and reports
        # an *empty* table name, so grants.py sees no object to check. DuckDB
        # reads the same text as `TRUNCATE TABLE "database" AS d` and empties a
        # real table. Reject the form outright rather than ship that divergence.
        raise StatementNotAllowed("TRUNCATE DATABASE is not permitted", "truncate_database")
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
) -> list[exp.Expression]:
    """Raise ``StatementNotAllowed`` if any statement in ``sql`` violates policy.

    Returns the statements it parsed, so a caller needing the same AST — the
    session path classifies each statement for History — can reuse it rather
    than parsing the text a second time.

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
    return parsed
