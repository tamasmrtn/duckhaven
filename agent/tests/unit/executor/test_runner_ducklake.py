"""The DuckLake attach path: what SQL the runner emits, and what it must not.

A recording fake connection, so these run with no infrastructure; the real
ATTACH is covered by agent/tests/integration/test_ducklake_roundtrip.py.
"""

from __future__ import annotations

import pytest

from agent.executor import runner


class FakeConn:
    """Records statements and bind lists. `stored_options` is what
    `ducklake_options()` returns, as DuckLake stores it."""

    def __init__(self, stored_options: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, list]] = []
        self.stored_options = dict(stored_options or {})
        self._rows: list[tuple] = []

    def execute(self, sql: str, params: list | None = None):
        self.calls.append((sql, params or []))
        self._rows = list(self.stored_options.items()) if "ducklake_options" in sql else []
        return self

    def fetchall(self) -> list[tuple]:
        return self._rows

    def sql_text(self) -> str:
        return "\n".join(sql for sql, _ in self.calls)


def _ducklake_catalog(slug: str = "raw") -> dict:
    return {
        "slug": slug,
        "kind": "ducklake",
        "polaris_name": "",
        "backend": {"kind": "object_store", "root_uri": ""},
        "default_schema": "analytics",
        "data_path": f"s3://warehouse/{slug}/",
        "metadata_schema": f"cat_{slug}",
        "meta": {
            "host": "postgres",
            "port": 5432,
            "database": "ducklake",
            "user": "ducklake_agent",
            "password": "s3cr3t-pw",
        },
        "options": {"target_file_size": "512MB", "data_inlining_row_limit": "10"},
        "storage": {
            "type": "s3",
            "scope": f"s3://warehouse/{slug}/",
            "url_style": "path",
            "key_id": "k",
            "secret": "s",
            "session_token": "",
            "region": "us-east-1",
            "endpoint": "objectstore:9000",
            "use_ssl": False,
        },
    }


def _iceberg_catalog(slug: str = "ice") -> dict:
    return {
        "slug": slug,
        "kind": "iceberg_polaris",
        "polaris_name": slug,
        "backend": {"kind": "object_store", "root_uri": ""},
        "default_schema": "analytics",
    }


_POLARIS = {"endpoint": "http://polaris:8181", "client_id": "root", "client_secret": "pw"}


def test_ducklake_attach_emits_the_expected_statements():
    conn = FakeConn()
    runner._attach_ducklake(conn, _ducklake_catalog())
    text = conn.sql_text()
    assert "CREATE OR REPLACE SECRET dh_dl_meta_raw" in text
    assert "TYPE POSTGRES" in text
    assert "CREATE OR REPLACE SECRET dh_dl_store_raw" in text
    assert "ATTACH 'ducklake:postgres:dbname=ducklake host=postgres port=5432'" in text
    assert "METADATA_SCHEMA 'cat_raw'" in text
    assert "META_SECRET 'dh_dl_meta_raw'" in text
    assert 'CREATE SCHEMA IF NOT EXISTS "raw"."analytics"' in text


def test_the_postgres_password_never_appears_in_statement_text():
    """DuckLake echoes the connection string on a failed attach, so an inline
    password would surface in errors and logs. It is a secret bind parameter."""
    conn = FakeConn()
    runner._attach_ducklake(conn, _ducklake_catalog())
    assert "s3cr3t-pw" not in conn.sql_text()
    # ...but it is passed as a bind.
    assert any("s3cr3t-pw" in [str(p) for p in params] for _, params in conn.calls)


def test_the_storage_secret_is_scoped_to_the_catalog_prefix():
    """All of a workspace's catalogs share one connection, so SCOPE keeps one
    catalog's credential off another's data."""
    conn = FakeConn()
    runner._attach_ducklake(conn, _ducklake_catalog("raw"))
    secret_call = next(c for c in conn.calls if "TYPE S3" in c[0])
    assert "s3://warehouse/raw/" in [str(p) for p in secret_call[1]]
    assert "SCOPE" in secret_call[0]


def test_secret_names_are_per_catalog():
    """Two DuckLake catalogs on one connection must not collide."""
    conn = FakeConn()
    runner._attach_ducklake(conn, _ducklake_catalog("raw"))
    runner._attach_ducklake(conn, _ducklake_catalog("curated"))
    text = conn.sql_text()
    assert "dh_dl_meta_raw" in text and "dh_dl_meta_curated" in text
    assert "dh_dl_store_raw" in text and "dh_dl_store_curated" in text


def test_azure_storage_uses_an_azure_secret():
    conn = FakeConn()
    cat = _ducklake_catalog()
    cat["storage"] = {
        "type": "azure",
        "account_name": "acct",
        "connection_string": "BlobEndpoint=https://x;SharedAccessSignature=sig",
    }
    runner._attach_ducklake(conn, cat)
    assert "TYPE AZURE" in conn.sql_text()
    assert "TYPE S3" not in conn.sql_text()


def test_a_ducklake_only_workspace_creates_no_iceberg_secret():
    """Polaris-free deployment: no Iceberg secret is created at all."""
    conn = FakeConn()
    runner._attach_catalogs(conn, catalogs=[_ducklake_catalog()], active_catalog="raw", polaris={})
    text = conn.sql_text()
    assert runner._ICEBERG_SECRET not in text
    assert "ATTACH 'ducklake:" in text
    assert 'USE "raw"."analytics"' in text


def test_a_mixed_workspace_attaches_both_kinds():
    conn = FakeConn()
    runner._attach_catalogs(
        conn,
        catalogs=[_iceberg_catalog(), _ducklake_catalog()],
        active_catalog="raw",
        polaris=_POLARIS,
    )
    text = conn.sql_text()
    assert "TYPE ICEBERG" in text
    assert "ATTACH 'ducklake:" in text
    assert runner._ICEBERG_SECRET in text


def test_an_iceberg_only_workspace_is_attached_exactly_as_before():
    """The Iceberg path is unchanged."""
    conn = FakeConn()
    runner._attach_catalogs(
        conn, catalogs=[_iceberg_catalog()], active_catalog="ice", polaris=_POLARIS
    )
    text = conn.sql_text()
    assert "ATTACH 'ice' AS \"ice\"" in text
    assert "ACCESS_DELEGATION_MODE 'vended_credentials'" in text
    assert "ducklake" not in text


def test_one_broken_catalog_does_not_fail_the_others():
    """Attach is per-catalog best-effort."""
    broken = _ducklake_catalog("broken")
    del broken["meta"]  # KeyError inside _attach_ducklake
    conn = FakeConn()
    runner._attach_catalogs(
        conn,
        catalogs=[broken, _iceberg_catalog()],
        active_catalog="ice",
        polaris=_POLARIS,
    )
    assert "ATTACH 'ice'" in conn.sql_text()


def test_retry_settings_are_pinned_and_not_user_changeable():
    """Set before the sandbox locks configuration, and absent from
    `_ALLOWED_CONFIGS` so a statement cannot change its own retries."""
    conn = FakeConn()
    runner._configure_ducklake(conn)
    text = conn.sql_text()
    assert "SET ducklake_max_retry_count = 10" in text
    assert "SET ducklake_retry_backoff = 1.5" in text
    assert "SET ducklake_retry_wait_ms = 100" in text
    for name in ("ducklake_max_retry_count", "ducklake_retry_backoff", "ducklake_retry_wait_ms"):
        assert name not in runner._ALLOWED_CONFIGS


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("iceberg_polaris", ("iceberg",)), ("ducklake", ("ducklake", "postgres"))],
)
def test_catalog_kind_extensions_use_the_install_name(kind, expected):
    """The runner installs `postgres`; the control plane matches the advertised
    `postgres_scanner`."""
    assert runner._CATALOG_KIND_EXTENSIONS[kind] == expected


def test_catalog_options_are_applied_with_bound_arguments():
    """The API's tuning settings are applied on attach, as bound parameters."""
    conn = FakeConn()
    runner._attach_ducklake(conn, _ducklake_catalog())

    option_calls = [c for c in conn.calls if "set_option" in c[0]]
    assert [c[1] for c in option_calls] == [
        ["target_file_size", "512MB"],
        ["data_inlining_row_limit", "10"],
    ]
    assert all("512MB" not in sql for sql, _ in option_calls)


def test_an_unknown_catalog_option_does_not_cost_us_the_attach():
    """An older extension that rejects an option must not fail every query."""

    class RejectingConn(FakeConn):
        def execute(self, sql: str, params: list | None = None):
            if "set_option" in sql:
                raise RuntimeError("Catalog Error: unrecognized option")
            return super().execute(sql, params)

    conn = RejectingConn()
    runner._attach_ducklake(conn, _ducklake_catalog())

    # The attach and the default namespace still happened.
    assert "ATTACH" in conn.sql_text()
    assert "CREATE SCHEMA IF NOT EXISTS" in conn.sql_text()


def _set_option_calls(conn: FakeConn) -> list[list]:
    return [params for sql, params in conn.calls if "set_option" in sql]


def test_unchanged_catalog_options_are_not_rewritten():
    """Every attach runs this; rewriting unchanged rows made concurrent attaches
    collide on `ducklake_metadata`. `512MB` is stored as `512000000`."""
    conn = FakeConn({"target_file_size": "512000000", "data_inlining_row_limit": "10"})
    runner._attach_ducklake(conn, _ducklake_catalog())

    assert _set_option_calls(conn) == []


def test_only_a_changed_catalog_option_is_written():
    conn = FakeConn({"target_file_size": "256000000", "data_inlining_row_limit": "10"})
    runner._attach_ducklake(conn, _ducklake_catalog())

    assert _set_option_calls(conn) == [["target_file_size", "512MB"]]


def test_losing_a_concurrent_write_to_the_same_value_is_not_a_failure(caplog):
    """Another attach wrote the value between our read and our write."""

    class RacingConn(FakeConn):
        def execute(self, sql: str, params: list | None = None):
            if "set_option" in sql:
                self.stored_options["target_file_size"] = "512000000"
                raise RuntimeError("could not serialize access due to concurrent update")
            return super().execute(sql, params)

    conn = RacingConn({"target_file_size": "256000000", "data_inlining_row_limit": "10"})
    with caplog.at_level("WARNING"):
        runner._attach_ducklake(conn, _ducklake_catalog())

    assert not [r for r in caplog.records if "Could not set DuckLake option" in r.message]


def test_options_are_written_when_they_cannot_be_read():
    """An extension without `ducklake_options()` falls back to writing them all."""

    class NoReadConn(FakeConn):
        def execute(self, sql: str, params: list | None = None):
            if "ducklake_options" in sql:
                raise RuntimeError("Catalog Error: Table Function ducklake_options does not exist")
            return super().execute(sql, params)

    conn = NoReadConn()
    runner._attach_ducklake(conn, _ducklake_catalog())

    assert len(_set_option_calls(conn)) == 2


@pytest.mark.parametrize(
    ("value", "stored"),
    [
        ("512MB", "512000000"),
        ("1GB", "1000000000"),
        ("2 MiB", "2097152"),
        ("1.5kb", "1500"),
        ("10", "10"),
        ("true", "true"),
    ],
)
def test_option_values_normalise_to_the_stored_form(value, stored):
    assert runner._normalise_option_value(value) == stored


def test_a_catalog_with_no_options_block_attaches_cleanly():
    """An agent may outlive the API version that started sending options."""
    conn = FakeConn()
    cat = _ducklake_catalog()
    del cat["options"]
    runner._attach_ducklake(conn, cat)

    assert "ATTACH" in conn.sql_text()
    assert not [c for c in conn.calls if "set_option" in c[0]]
