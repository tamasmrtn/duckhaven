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
    # The bundled MinIO is plain HTTP, so no CA bundle is configured.
    assert not any("ca_cert_file" in c for c in cmds)


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
    # External S3 is HTTPS: a CA bundle is set, but the azure-only transport is not.
    assert any(c.startswith("SET ca_cert_file =") for c in cmds)
    assert not any("azure_transport_option_type" in c for c in cmds)
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
    # ADLS is HTTPS and its extension only honours the CA bundle under the curl
    # transport, so both are configured (the SSL-CA fix found in real-Azure testing).
    assert any(c.startswith("SET ca_cert_file =") for c in cmds)
    assert any("SET azure_transport_option_type = 'curl'" in c for c in cmds)


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


# --- Credential-expiry re-vend + retry (G-D-cred-refresh) --------------------

_EXPIRED_S3 = (
    "HTTP Error: GetTableInformation endpoint returned response code Forbidden_403 with "
    'message "The Access Key Id you provided does not exist in our records."'
)


class RetryConn:
    """A FakeConn that optionally raises a credential error on the COPY."""

    def __init__(self, fail_on_copy: bool) -> None:
        self.commands: list[str] = []
        self._fail_on_copy = fail_on_copy

    def execute(self, sql: str, params: list[Any] | None = None):
        self.commands.append(sql)
        if self._fail_on_copy and sql.startswith("COPY"):
            raise RuntimeError(_EXPIRED_S3)
        return self

    def fetchone(self):
        return (0,)

    def close(self) -> None:
        pass


def _conn_factory(monkeypatch: pytest.MonkeyPatch, conns: list[RetryConn]) -> list[RetryConn]:
    """Hand out ``conns`` in order on each duckdb.connect(); record what was made."""
    made: list[RetryConn] = []
    it = iter(conns)

    def _connect(*_a, **_kw):
        c = next(it)
        made.append(c)
        return c

    monkeypatch.setattr(runner_module.duckdb, "connect", _connect)
    return made


def test_is_credential_error_matches_expiry_but_not_other_errors():
    assert runner_module._is_credential_error(RuntimeError(_EXPIRED_S3))
    assert runner_module._is_credential_error(Exception("InvalidToken: bad session token"))
    assert runner_module._is_credential_error(Exception("The provided token has expired"))
    assert not runner_module._is_credential_error(Exception("Catalog Error: Table x not found"))
    assert not runner_module._is_credential_error(Exception("Out of Memory"))


def test_expired_credentials_are_re_vended_and_retried_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # First connection's COPY fails with an expired-key S3 error; the runner
    # should re-vend a fresh connection and succeed on the retry.
    made = _conn_factory(monkeypatch, [RetryConn(fail_on_copy=True), RetryConn(fail_on_copy=False)])
    result = runner_module.run_query_sync(
        "SELECT 1",
        tmp_path / "out.parquet",
        memory_bytes=1024**3,
        threads=2,
        catalogs=[_catalog("ws_alpha", "ws-alpha", "object_store", "file:///tmp/data")],
        active_catalog="ws_alpha",
        polaris=POLARIS,
    )
    assert result["row_count"] == 0
    # Two connections were opened: the stale one and the re-vended one.
    assert len(made) == 2
    assert any(c.startswith("COPY") for c in made[1].commands)


def test_non_credential_error_is_not_retried(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    class BoomConn(RetryConn):
        def execute(self, sql: str, params: list[Any] | None = None):
            self.commands.append(sql)
            if sql.startswith("COPY"):
                raise RuntimeError("Catalog Error: Table with name users does not exist!")
            return self

    made = _conn_factory(monkeypatch, [BoomConn(fail_on_copy=False), RetryConn(fail_on_copy=False)])
    with pytest.raises(RuntimeError, match="does not exist"):
        runner_module.run_query_sync(
            "SELECT 1",
            tmp_path / "out.parquet",
            memory_bytes=1024**3,
            threads=2,
            catalogs=[_catalog("ws_alpha", "ws-alpha", "object_store", "file:///tmp/data")],
            active_catalog="ws_alpha",
            polaris=POLARIS,
        )
    # Only the first connection was opened — no re-vend on a non-credential error.
    assert len(made) == 1
