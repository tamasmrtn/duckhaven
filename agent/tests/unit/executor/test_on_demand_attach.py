"""Attaching catalogs after a connection is open: on a miss, and ahead of the lock.

The misses use real DuckDB with a second in-memory catalog standing in for an
Iceberg or DuckLake one, so the error text matched is the engine's own. The real
late ATTACH of both kinds is covered by the cross-component tests.
"""

from __future__ import annotations

import duckdb
import pytest

from agent.executor import runner

_POLARIS = {"endpoint": "http://polaris:8181", "client_id": "root", "client_secret": "pw"}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM other.main.t",
        "SELECT * FROM other.t",
        "DESCRIBE other.main.t",
        "INSERT INTO other.main.t VALUES (1)",
        "CREATE TABLE other.main.x AS SELECT 1",
        "USE other.main",
        "SELECT * FROM OTHER.main.t",
    ],
)
def test_the_missing_catalog_is_named_for_every_statement_shape(sql):
    conn = duckdb.connect()
    with pytest.raises(duckdb.Error) as exc:
        conn.execute(sql)
    assert runner.missing_catalog(exc.value) == "other"


def test_a_missing_table_is_not_a_missing_catalog():
    conn = duckdb.connect()
    with pytest.raises(duckdb.Error) as exc:
        conn.execute("SELECT * FROM nope")
    assert runner.missing_catalog(exc.value) is None


def _conn_with_a_view_over_other() -> duckdb.DuckDBPyConnection:
    """A view in the main catalog that reads catalog `other`, which is then detached:
    the shape the control plane cannot see, because the statement never names it."""
    conn = duckdb.connect()
    conn.execute("ATTACH ':memory:' AS other")
    conn.execute("CREATE TABLE other.main.t AS SELECT 42 AS v")
    conn.execute("CREATE VIEW v AS SELECT * FROM other.main.t")
    conn.execute("DETACH other")
    return conn


def _attach_other(asked: list[str]):
    def attach(conn, name):
        asked.append(name)
        conn.execute(f"ATTACH ':memory:' AS {name}")
        conn.execute(f"CREATE TABLE {name}.main.t AS SELECT 42 AS v")
        return True

    return attach


def _run(conn, sql, tmp_path, attach_missing):
    return runner._run_one_statement(
        conn,
        sql,
        tmp_path / "r.parquet",
        memory_bytes=256 * 1024**2,
        threads=1,
        enable_profiling=False,
        attach_missing=attach_missing,
    )


def test_a_catalog_only_a_view_reads_is_attached_and_the_statement_retried(tmp_path):
    conn = _conn_with_a_view_over_other()
    asked: list[str] = []

    result = _run(conn, "SELECT v FROM v", tmp_path, _attach_other(asked))

    assert asked == ["other"]
    assert result["row_count"] == 1


def test_a_refused_catalog_raises_the_original_error(tmp_path):
    conn = _conn_with_a_view_over_other()
    asked: list[str] = []

    def refuse(conn, name):
        asked.append(name)
        return False

    with pytest.raises(duckdb.BinderException, match='Catalog "other" does not exist'):
        _run(conn, "SELECT v FROM v", tmp_path, refuse)
    assert asked == ["other"]


def test_a_catalog_is_asked_for_only_once(tmp_path):
    """An attach that does not take must not loop."""
    conn = _conn_with_a_view_over_other()
    asked: list[str] = []

    def no_op(conn, name):
        asked.append(name)
        return True

    with pytest.raises(duckdb.BinderException):
        _run(conn, "SELECT v FROM v", tmp_path, no_op)
    assert asked == ["other"]


def test_a_script_is_never_retried(tmp_path):
    """Its earlier statements have already run when a later one fails to bind."""
    conn = _conn_with_a_view_over_other()
    asked: list[str] = []

    with pytest.raises(duckdb.BinderException):
        _run(conn, "CREATE TABLE made AS SELECT 1; SELECT v FROM v", tmp_path, _attach_other(asked))
    assert asked == []


def test_without_a_resolver_the_error_stands(tmp_path):
    conn = _conn_with_a_view_over_other()
    with pytest.raises(duckdb.BinderException):
        _run(conn, "SELECT v FROM v", tmp_path, None)


def _ducklake_descriptor(slug: str) -> dict:
    return {"slug": slug, "kind": "ducklake", "backend": {"kind": "object_store"}}


def test_attach_catalog_attaches_once(monkeypatch):
    conn = duckdb.connect()
    attached: list[str] = []

    def fake_attach_one(conn, cat, endpoint):
        attached.append(cat["slug"])
        conn.execute(f"ATTACH ':memory:' AS {cat['slug']}")

    monkeypatch.setattr(runner, "_attach_one", fake_attach_one)

    assert runner.attach_catalog(conn, _ducklake_descriptor("lake"), polaris=_POLARIS) is True
    assert runner.attach_catalog(conn, _ducklake_descriptor("lake"), polaris=_POLARIS) is False
    assert attached == ["lake"]


@pytest.mark.parametrize(("kind", "creates"), [("iceberg_polaris", True), ("ducklake", False)])
def test_a_late_iceberg_attach_creates_the_shared_secret_first(monkeypatch, kind, creates):
    """A connection opened with only DuckLake catalogs has no Iceberg secret yet."""
    conn = duckdb.connect()
    created: list[bool] = []
    monkeypatch.setattr(
        runner, "_create_iceberg_secret", lambda conn, polaris: created.append(True)
    )
    monkeypatch.setattr(
        runner,
        "_attach_one",
        lambda conn, cat, ep: conn.execute(f"ATTACH ':memory:' AS {cat['slug']}"),
    )

    runner.attach_catalog(conn, {"slug": "c", "kind": kind, "polaris_name": "c"}, polaris=_POLARIS)

    assert created == ([True] if creates else [])


class _RecordingConn:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql, *args, **kwargs):
        self.executed.append(sql)
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def close(self):
        pass


def test_every_workspace_kind_is_prepared_before_the_lock(monkeypatch):
    """A catalog attached after the lock needs its extension loaded and, for
    DuckLake, its settings applied, before the lock refuses `SET` -- and secret
    storage initialised before the filesystem latch can stop it."""
    conn = _RecordingConn()
    monkeypatch.setattr(runner.duckdb, "connect", lambda *a, **k: conn)

    runner.open_and_attach(
        catalogs=[
            {
                "slug": "ice",
                "kind": "iceberg_polaris",
                "polaris_name": "ice",
                "backend": {"kind": "object_store"},
            }
        ],
        active_catalog="ice",
        polaris=_POLARIS,
        lock_config=True,
        disabled_filesystems="LocalFileSystem",
        preload_catalog_kinds=["iceberg_polaris", "ducklake"],
        preload_backend_kinds=["object_store", "adls_gen2"],
    )

    sql = conn.executed
    lock_at = next(i for i, s in enumerate(sql) if "lock_configuration" in s)
    latch_at = next(i for i, s in enumerate(sql) if "disabled_filesystems" in s)
    for needed in ("LOAD ducklake", "LOAD postgres", "LOAD azure", "SET ducklake_max_retry_count"):
        at = next((i for i, s in enumerate(sql) if s.strip().startswith(needed)), None)
        assert at is not None and at < lock_at, f"{needed!r} missing or after the lock: {sql}"
    secrets_at = next(i for i, s in enumerate(sql) if "duckdb_secrets()" in s)
    assert secrets_at < latch_at
    # Only the catalog that was sent is attached.
    assert sum(s.lstrip().startswith("ATTACH") for s in sql) == 1
