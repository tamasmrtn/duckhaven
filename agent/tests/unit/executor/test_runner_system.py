"""The `duckhaven` system catalog's attach: what SQL the runner emits, and that it
never stands in for a workspace catalog.

A recording fake connection, so these run with no Postgres; the real attach, and
its read-only refusal of a write, are covered by the cross-component tests.
"""

from __future__ import annotations

import pytest

from agent import runtime
from agent.executor import runner


class FakeConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list]] = []

    def execute(self, sql: str, params: list | None = None):
        self.calls.append((sql, params or []))
        return self

    def fetchall(self) -> list[tuple]:
        return []

    def fetchone(self):
        return None

    def close(self) -> None:
        pass

    def sql(self) -> list[str]:
        return [sql for sql, _ in self.calls]


WS = "8f3c1f0e-5a0b-4c7e-9c3a-2d1e0f4b6a7c"
ANA = "0b6c2a7e-1d3f-4e5a-8b9c-0d1e2f3a4b5c"
ATTACH = (
    f"ATTACH 'options=''-c duckhaven.workspace={WS} -c duckhaven.principals={ANA} "
    "-c duckhaven.readable=access.audit,query.history''' AS \"duckhaven\" "
    "(TYPE postgres, SECRET dh_sys, READ_ONLY)"
)

_POLARIS = {"endpoint": "http://polaris:8181", "client_id": "root", "client_secret": "pw"}


def _system() -> dict:
    return {
        "slug": "duckhaven",
        "kind": "system",
        "backend": {},
        "default_schema": "access",
        "meta": {
            "host": "postgres",
            "port": 5432,
            "database": "duckhaven_system",
            "user": "dh_system_reader",
            "password": "reader-pw",
            "settings": {
                "duckhaven.workspace": WS,
                "duckhaven.principals": ANA,
                "duckhaven.readable": "access.audit,query.history",
            },
        },
    }


def _catalog(slug: str, kind: str) -> dict:
    return {
        "slug": slug,
        "kind": kind,
        "polaris_name": slug if kind == "iceberg_polaris" else "",
        "backend": {"kind": "object_store"},
        "default_schema": "analytics",
    }


def test_it_is_attached_read_only_through_postgres(monkeypatch):
    monkeypatch.setattr(runtime, "SECRET_BIND_PARAMETERS", True)
    conn = FakeConn()

    runner._attach_one(conn, _system(), "")

    secret, attach = conn.calls
    assert secret[0].startswith("CREATE OR REPLACE SECRET dh_sys (TYPE POSTGRES, HOST ?")
    assert secret[1] == ["postgres", 5432, "duckhaven_system", "dh_system_reader", "reader-pw"]
    assert attach == (ATTACH, [])


def test_who_it_runs_for_goes_in_the_connection_string_the_store_filters_by():
    conn = FakeConn()

    runner._attach_system(conn, _system())

    attach = conn.sql()[-1]
    assert f"-c duckhaven.workspace={WS}" in attach
    assert f"-c duckhaven.principals={ANA}" in attach
    assert "-c duckhaven.readable=access.audit,query.history" in attach


def test_a_setting_that_could_reach_past_its_own_value_attaches_nothing():
    conn = FakeConn()
    system = _system()
    system["meta"]["settings"]["duckhaven.readable"] = "access.audit' AS x --"

    with pytest.raises(ValueError, match="Invalid value"):
        runner._attach_system(conn, system)

    assert conn.calls == []


def test_without_settings_it_attaches_reading_nothing():
    """What an older control plane sends: the store's policies then match no row."""
    conn = FakeConn()
    system = _system()
    del system["meta"]["settings"]

    runner._attach_system(conn, system)

    assert conn.sql()[-1].startswith(
        "ATTACH 'options=''-c duckhaven.workspace= -c duckhaven.principals= "
        "-c duckhaven.readable=''' AS"
    )


def test_the_password_never_appears_in_statement_text(monkeypatch):
    """Also where the engine takes no bind parameters (DuckDB 2.0)."""
    monkeypatch.setattr(runtime, "SECRET_BIND_PARAMETERS", False)
    conn = FakeConn()
    failing = {"n": 0}

    def execute(sql, params=None):
        conn.calls.append((sql, params or []))
        if "SECRET" in sql and failing["n"] == 0:
            failing["n"] += 1
            raise runner.duckdb.InvalidInputException(f"bad option in: {sql}")
        return conn

    conn.execute = execute  # type: ignore[method-assign]

    with pytest.raises(runner.duckdb.InvalidInputException) as exc:
        runner._attach_system(conn, _system())

    assert "reader-pw" not in str(exc.value)


@pytest.mark.parametrize(
    ("others", "iceberg_secret"),
    [([("ice", "iceberg_polaris")], True), ([("lake", "ducklake")], False), ([], False)],
)
def test_it_needs_no_iceberg_secret(monkeypatch, others, iceberg_secret):
    created: list[bool] = []
    monkeypatch.setattr(runner, "_create_iceberg_secret", lambda conn, p: created.append(True))
    monkeypatch.setattr(runner, "_attach_ducklake", lambda conn, cat: None)
    conn = FakeConn()
    catalogs = [_catalog(slug, kind) for slug, kind in others] + [_system()]

    runner._attach_catalogs(
        conn, catalogs=catalogs, active_catalog=None, polaris=_POLARIS if others else {}
    )

    assert created == ([True] if iceberg_secret else [])


@pytest.mark.parametrize("active", ["duckhaven", "gone", None])
def test_it_is_never_where_unqualified_names_resolve(monkeypatch, active):
    monkeypatch.setattr(runner, "_create_iceberg_secret", lambda conn, p: None)
    conn = FakeConn()

    runner._attach_catalogs(
        conn,
        catalogs=[_system(), _catalog("ice", "iceberg_polaris")],
        active_catalog=active,
        polaris=_POLARIS,
    )

    assert [s for s in conn.sql() if s.startswith("USE")] == ['USE "ice"."analytics"']


def test_alone_it_is_attached_but_never_used(monkeypatch):
    conn = FakeConn()

    runner._attach_catalogs(conn, catalogs=[_system()], active_catalog=None, polaris={})

    assert ATTACH in conn.sql()
    assert not any(s.startswith("USE") for s in conn.sql())


@pytest.mark.parametrize(("kind", "creates"), [("iceberg_polaris", True), ("system", False)])
def test_a_late_attach_creates_the_iceberg_secret_only_for_iceberg(monkeypatch, kind, creates):
    created: list[bool] = []
    monkeypatch.setattr(runner, "_create_iceberg_secret", lambda conn, p: created.append(True))
    monkeypatch.setattr(runner, "is_attached", lambda conn, slug: False)
    monkeypatch.setattr(runner, "_attach_one", lambda conn, cat, endpoint: None)
    conn = FakeConn()
    conn.fetchone = lambda: (0,)  # type: ignore[method-assign]

    runner.attach_catalog(conn, {**_system(), "kind": kind}, polaris=_POLARIS)

    assert created == ([True] if creates else [])


def test_a_session_that_may_read_it_loads_postgres_before_the_lock(monkeypatch):
    """The catalog comes with a later statement, after the configuration lock,
    when `LOAD` is refused."""
    conn = FakeConn()
    monkeypatch.setattr(runner.duckdb, "connect", lambda *a, **k: conn)

    runner.open_and_attach(
        catalogs=[_catalog("ice", "iceberg_polaris")],
        active_catalog="ice",
        polaris=_POLARIS,
        lock_config=True,
        preload_catalog_kinds=["iceberg_polaris", "system"],
        preload_backend_kinds=["object_store"],
    )

    sql = conn.sql()
    lock_at = next(i for i, s in enumerate(sql) if "lock_configuration" in s)
    load_at = next((i for i, s in enumerate(sql) if s.strip().startswith("LOAD postgres")), None)
    assert load_at is not None and load_at < lock_at, sql


def test_with_no_polaris_a_one_shot_still_attaches_it(monkeypatch):
    """A DuckLake-only workspace gets no Polaris block."""
    conn = FakeConn()
    monkeypatch.setattr(runner.duckdb, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(runner, "_attach_ducklake", lambda conn, cat: None)

    runner.open_and_attach(
        catalogs=[_catalog("lake", "ducklake"), _system()], active_catalog="lake", polaris=None
    )

    assert ATTACH in conn.sql()


def test_the_agent_advertises_it():
    """The API sends the system catalog only to an agent that says it can attach it."""
    from agent.control import channel as ch

    assert "system_catalog" in ch._PROTOCOL_FEATURES
    assert "system_catalog_row_filter" in ch._PROTOCOL_FEATURES
