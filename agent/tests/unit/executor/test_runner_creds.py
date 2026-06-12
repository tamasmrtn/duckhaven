"""Runner Polaris/Iceberg attach + extension wiring.

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
        backend={"kind": "object_store", "root_uri": "file:///tmp/data"},
        workspace_slug="ws-alpha",
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
        backend={"kind": "s3", "root_uri": "s3://bucket/prefix"},
        workspace_slug="ws-alpha",
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
        backend={"kind": "adls_gen2", "root_uri": "abfss://c@a.dfs/"},
        workspace_slug="ws-blue",
        polaris=POLARIS,
    )
    cmds = [c[0] for c in fake_conn.commands]
    assert any("INSTALL azure" in c for c in cmds)
    assert any("LOAD azure" in c for c in cmds)
    attach_cmd = next(c for c in cmds if c.startswith("ATTACH"))
    assert "ACCESS_DELEGATION_MODE 'vended_credentials'" in attach_cmd


def test_attach_uses_workspace_slug_as_warehouse(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_bytes=1024**3,
        threads=2,
        backend={"kind": "object_store", "root_uri": "file:///tmp/data"},
        workspace_slug="ws-alpha",
        polaris=POLARIS,
    )
    attach_sql = next(c[0] for c in fake_conn.commands if c[0].startswith("ATTACH"))
    # ATTACH takes no bind params; warehouse (slug) + endpoint are inlined.
    assert "ATTACH 'ws-alpha' AS dh_catalog" in attach_sql
    assert "ENDPOINT 'http://polaris:8181/api/catalog'" in attach_sql


def test_no_polaris_means_no_attach(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_bytes=1024**3,
        threads=2,
        workspace_slug="ws-alpha",
    )
    cmds = [c[0] for c in fake_conn.commands]
    assert not any("ATTACH" in c for c in cmds)


def test_no_workspace_slug_means_no_attach(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_bytes=1024**3,
        threads=2,
        polaris=POLARIS,
    )
    cmds = [c[0] for c in fake_conn.commands]
    assert not any("ATTACH" in c for c in cmds)
