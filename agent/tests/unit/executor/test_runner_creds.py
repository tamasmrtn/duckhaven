"""M3 Step 10 — runner cred + extension wiring.

Uses a fake `duckdb.connect` to capture the SQL the runner issues
without depending on the real extensions or real cloud creds. The real
DuckDB path is exercised by the existing `test_runner.py` (local-fs
SELECT) and by the S3 integration spike.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent.executor import runner as runner_module


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


def test_local_fs_skips_secret_and_extensions(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_limit_gb=1.0,
        backend={"kind": "local_fs", "root_uri": "file:///tmp/data"},
        storage_credentials=None,
    )
    cmds = [c[0] for c in fake_conn.commands]
    # Memory limit set; no extension installs; no CREATE SECRET.
    assert any("memory_limit" in c for c in cmds)
    assert not any("INSTALL" in c for c in cmds)
    assert not any("CREATE SECRET" in c for c in cmds)


def test_s3_loads_httpfs_and_creates_secret_with_scope(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_limit_gb=1.0,
        backend={"kind": "s3", "root_uri": "s3://bucket/prefix"},
        storage_credentials={
            "kind": "s3",
            "fields": {
                "access_key_id": "AKIA…",
                "secret_access_key": "secret",
                "session_token": "tok",
                "region": "us-east-1",
            },
            "expires_at": "2099-01-01T00:00:00Z",
        },
        workspace_slug="ws-alpha",
    )
    cmds = [c[0] for c in fake_conn.commands]
    params_by_cmd = dict(fake_conn.commands)

    assert any("INSTALL httpfs" in c for c in cmds)
    assert any("LOAD httpfs" in c for c in cmds)

    secret_cmd = next(c for c in cmds if c.startswith("CREATE SECRET"))
    # Secret named after the workspace slug, sanitized for DuckDB identifiers.
    assert "ws_ws_alpha" in secret_cmd
    assert "TYPE S3" in secret_cmd
    assert "KEY_ID ?" in secret_cmd
    assert "SECRET ?" in secret_cmd
    assert "SESSION_TOKEN ?" in secret_cmd
    assert "REGION ?" in secret_cmd
    assert "SCOPE ?" in secret_cmd
    # Params line up with the field order in the SQL.
    params = params_by_cmd[secret_cmd]
    assert params == [
        "AKIA…",
        "secret",
        "tok",
        "us-east-1",
        "s3://bucket/prefix",
    ]


def test_adls_loads_azure_and_creates_secret(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_limit_gb=1.0,
        backend={"kind": "adls_gen2", "root_uri": "abfss://c@a.dfs/"},
        storage_credentials={
            "kind": "azure",
            "fields": {"connection_string": "DefaultEndpoints=..."},
            "expires_at": "2099-01-01T00:00:00Z",
        },
        workspace_slug="ws-blue",
    )
    cmds = [c[0] for c in fake_conn.commands]
    assert any("INSTALL azure" in c for c in cmds)
    assert any("LOAD azure" in c for c in cmds)
    secret_cmd = next(c for c in cmds if c.startswith("CREATE SECRET"))
    assert "TYPE AZURE" in secret_cmd
    assert "CONNECTION_STRING ?" in secret_cmd


def test_uc_attach_when_endpoint_provided(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_limit_gb=1.0,
        backend={"kind": "local_fs", "root_uri": "file:///tmp/data"},
        workspace_slug="ws-alpha",
        uc_endpoint="http://uc:8080",
    )
    cmds = [c[0] for c in fake_conn.commands]
    assert any("INSTALL unity_catalog" in c for c in cmds)
    assert any("ATTACH" in c and "UC_CATALOG" in c for c in cmds)


def test_no_workspace_slug_means_no_attach(fake_conn: FakeConn, tmp_path: Path):
    runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_limit_gb=1.0,
    )
    cmds = [c[0] for c in fake_conn.commands]
    assert not any("ATTACH" in c for c in cmds)
