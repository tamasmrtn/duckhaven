"""Runner Polaris/Iceberg multi-attach + extension wiring.

Uses a fake `duckdb.connect` to capture the SQL the runner issues
without depending on the real extensions or a live Polaris. The real
DuckDB path is exercised by `test_runner.py` (local-fs SELECT) and by
the Polaris integration test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.executor import runner as runner_module

POLARIS = {
    "endpoint": "http://polaris:8181",
    "client_id": "root",
    "client_secret": "s3cr3t",
}


def _catalog(slug: str, polaris_name: str, kind: str, root_uri: str) -> dict[str, Any]:
    return {
        "slug": slug,
        "polaris_name": polaris_name,
        "backend": {"kind": kind, "root_uri": root_uri},
        "default_schema": "analytics",
    }


class FakeConn:
    def __init__(self) -> None:
        self.commands: list[tuple[str, list[Any]]] = []

    def execute(self, sql: str, params: list[Any] | None = None):
        self.commands.append((sql, list(params or [])))
        return self

    def fetchone(self):
        # `SELECT count(*) FROM read_parquet(...)` is the only fetchone
        # site in the runner; pretend we wrote zero rows.
        return (0,)

    def close(self) -> None:
        pass


@pytest.fixture
def fake_conn(monkeypatch: pytest.MonkeyPatch) -> FakeConn:
    conn = FakeConn()
    monkeypatch.setattr(runner_module.duckdb, "connect", lambda *a, **kw: conn)
    return conn


def test_object_store_loads_httpfs_and_vends_credentials(fake_conn: FakeConn, tmp_path: Path):
    # object_store is backed by the bundled MinIO (S3): it loads httpfs and
    # uses vended credentials, exactly like the s3 kind.
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_bytes=1024**3,
        threads=2,
        catalogs=[_catalog("ws_alpha", "ws-alpha", "object_store", "file:///tmp/data")],
        active_catalog="ws_alpha",
        polaris=POLARIS,
    )
    cmds = [c[0] for c in fake_conn.commands]
    assert any("INSTALL iceberg" in c for c in cmds)
    assert any("INSTALL httpfs" in c for c in cmds)
    assert any("LOAD httpfs" in c for c in cmds)
    attach_cmd = next(c for c in cmds if c.startswith("ATTACH"))
    assert "ACCESS_DELEGATION_MODE 'vended_credentials'" in attach_cmd


def test_s3_loads_httpfs_and_vends_credentials(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_bytes=1024**3,
        threads=2,
        catalogs=[_catalog("ws_alpha", "ws-alpha", "s3", "s3://bucket/prefix")],
        active_catalog="ws_alpha",
        polaris=POLARIS,
    )
    cmds = [c[0] for c in fake_conn.commands]
    assert any("INSTALL httpfs" in c for c in cmds)
    assert any("LOAD httpfs" in c for c in cmds)
    assert any("INSTALL iceberg" in c for c in cmds)
    attach_cmd = next(c for c in cmds if c.startswith("ATTACH"))
    assert "ACCESS_DELEGATION_MODE 'vended_credentials'" in attach_cmd
    # The iceberg secret carries the OAuth2 client credentials.
    secret_cmd, secret_params = next(
        c for c in fake_conn.commands if c[0].startswith("CREATE SECRET")
    )
    assert "OAUTH2_SERVER_URI ?" in secret_cmd
    assert secret_params == ["root", "s3cr3t", "http://polaris:8181/api/catalog/v1/oauth/tokens"]


def test_adls_loads_azure_and_vends_credentials(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_bytes=1024**3,
        threads=2,
        catalogs=[_catalog("ws_blue", "ws-blue", "adls_gen2", "abfss://c@a.dfs/")],
        active_catalog="ws_blue",
        polaris=POLARIS,
    )
    cmds = [c[0] for c in fake_conn.commands]
    assert any("INSTALL azure" in c for c in cmds)
    assert any("LOAD azure" in c for c in cmds)
    attach_cmd = next(c for c in cmds if c.startswith("ATTACH"))
    assert "ACCESS_DELEGATION_MODE 'vended_credentials'" in attach_cmd


def test_attach_uses_polaris_name_as_warehouse_and_slug_alias(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_bytes=1024**3,
        threads=2,
        catalogs=[_catalog("ws_alpha", "ws-alpha", "object_store", "file:///tmp/data")],
        active_catalog="ws_alpha",
        polaris=POLARIS,
    )
    attach_sql = next(c[0] for c in fake_conn.commands if c[0].startswith("ATTACH"))
    # ATTACH takes no bind params; the Polaris warehouse name + endpoint are
    # inlined, and the catalog is aliased by its (identifier-safe) slug.
    assert "ATTACH 'ws-alpha' AS \"ws_alpha\"" in attach_sql
    assert "ENDPOINT 'http://polaris:8181/api/catalog'" in attach_sql
    use_sql = next(c[0] for c in fake_conn.commands if c[0].startswith("USE"))
    assert use_sql == 'USE "ws_alpha"."analytics"'


def test_multi_attach_attaches_every_catalog_and_uses_active(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_bytes=1024**3,
        threads=2,
        catalogs=[
            _catalog("raw", "dev__raw", "object_store", "file:///tmp/raw"),
            _catalog("curated", "dev__curated", "object_store", "file:///tmp/curated"),
        ],
        active_catalog="curated",
        polaris=POLARIS,
    )
    attaches = [c[0] for c in fake_conn.commands if c[0].startswith("ATTACH")]
    assert any("ATTACH 'dev__raw' AS \"raw\"" in c for c in attaches)
    assert any("ATTACH 'dev__curated' AS \"curated\"" in c for c in attaches)
    use_sql = next(c[0] for c in fake_conn.commands if c[0].startswith("USE"))
    assert use_sql == 'USE "curated"."analytics"'


def test_read_only_catalog_attaches_read_only(fake_conn: FakeConn, tmp_path: Path):
    """A descriptor flagged read_only (the system catalog) is ATTACHed READ_ONLY;
    a normal catalog is not."""
    system = _catalog("duckhaven", "duckhaven", "object_store", "")
    system["read_only"] = True
    user_cat = _catalog("sales", "sales", "object_store", "file:///tmp/sales")
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_bytes=1024**3,
        threads=2,
        catalogs=[user_cat, system],
        active_catalog="sales",
        polaris=POLARIS,
    )
    attaches = [c[0] for c in fake_conn.commands if c[0].startswith("ATTACH")]
    system_attach = next(c for c in attaches if 'AS "duckhaven"' in c)
    user_attach = next(c for c in attaches if 'AS "sales"' in c)
    assert "READ_ONLY" in system_attach
    assert "READ_ONLY" not in user_attach


def test_no_polaris_means_no_attach(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_bytes=1024**3,
        threads=2,
        catalogs=[_catalog("ws_alpha", "ws-alpha", "object_store", "file:///tmp/data")],
    )
    cmds = [c[0] for c in fake_conn.commands]
    assert not any("ATTACH" in c for c in cmds)


def test_no_catalogs_means_no_attach(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_bytes=1024**3,
        threads=2,
        polaris=POLARIS,
    )
    cmds = [c[0] for c in fake_conn.commands]
    assert not any("ATTACH" in c for c in cmds)
